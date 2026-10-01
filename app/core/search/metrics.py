"""Search response cache + durable metrics (SQLite via the existing
Database layer - no second storage mechanism, no Redis).
"""
from __future__ import annotations

import json
import time

from app.core.storage.db import Database

DEFAULT_TTL_S = 3600.0


class SearchCache:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, ttl_s: float = DEFAULT_TTL_S):
        """Stored (response_json, created_at) while fresh; None on
        miss/expiry. Expiry rows are deleted lazily."""
        with self.db.cursor() as cur:
            row = cur.execute(
                "SELECT response_json, created_at FROM search_cache WHERE key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            if time.time() - row["created_at"] > ttl_s:
                cur.execute("DELETE FROM search_cache WHERE key = ?", (key,))
                return None
            return json.loads(row["response_json"])

    def put(self, key: str, response_json: str) -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO search_cache (key, response_json, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET response_json = excluded.response_json, "
                "created_at = excluded.created_at",
                (key, response_json, time.time()),
            )


class SearchMetrics:
    """Durable per-request records + derived counters. This is how the
    user answers 'why did this ICP search return nothing?'."""

    def __init__(self, db: Database):
        self.db = db

    def record(self, request_id: str, query: str, page: int, provider: str,
               status: str, latency_ms: int, num_results: int, error: str = "") -> None:
        with self.db.cursor() as cur:
            cur.execute(
                "INSERT INTO search_log (request_id, query, page, provider, status, "
                "latency_ms, num_results, error, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, query, page, provider, status, latency_ms,
                 num_results, error, time.time()),
            )

    def snapshot(self, limit: int = 200) -> dict:
        """Aggregate counters over recent requests + per-provider health."""
        with self.db.cursor() as cur:
            total, ok, cached, failed, empty = cur.execute(
                "SELECT COUNT(*), "
                "SUM(status IN ('ok','cached')), SUM(status = 'cached'), "
                "SUM(status = 'failed'), SUM(num_results = 0) FROM "
                "(SELECT * FROM search_log ORDER BY id DESC LIMIT ?)", (limit,)
            ).fetchone()
            per_provider = cur.execute(
                "SELECT provider, COUNT(*), SUM(status = 'failed') FROM "
                "(SELECT * FROM search_log ORDER BY id DESC LIMIT ?) "
                "GROUP BY provider", (limit,)
            ).fetchall()
        return {
            "requests": total or 0,
            "served": (ok or 0),
            "cache_hits": (cached or 0),
            "failures": (failed or 0),
            "empty_results": (empty or 0),
            "providers": [
                {"provider": p, "requests": n, "failures": f or 0}
                for p, n, f in per_provider
            ],
        }
