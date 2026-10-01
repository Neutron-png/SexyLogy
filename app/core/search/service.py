"""
SearchService - the ONLY entry point LOGY uses for web search.

Caller contract (in-process, sync):

    service = SearchService(db)
    resp = service.search(SearchRequest(query="...", country="us"))
    resp.results.organic  -> normalized, deduped, ranking-preserving

Internals: cache-first -> provider chain (primary, then fallbacks by
capability) -> normalize -> cache -> durable log/metrics. Provider
health + circuit breaker per provider; retries with backoff only for
transient (timeout/network) failures; a BLOCKED provider is never
hammered - it falls back or fails gracefully (no CAPTCHA bypassing,
by design).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Optional

from app.core.search import urls as urls_mod
from app.core.search.metrics import SearchCache, SearchMetrics, DEFAULT_TTL_S
from app.core.search.providers import (
    DDGHTMLProvider, ProviderFailure, SearXNGProvider,
    http_client,
)
from app.core.search.schema import (
    new_request_id, ProviderCapabilities, SearchRequest, SearchResponse,
    ResultBlocks,
)

DEFAULT_CONFIG = {
    "primary": "auto",          # auto | ddg_html | searxng
    "searxng_base_url": "",     # empty = SearXNG not configured
    "cache_ttl_s": DEFAULT_TTL_S,
    "min_interval_s": 1.5,      # per-provider request pacing
    "max_concurrency": 2,
    "timeout_s": 15.0,
    "retries": 2,               # for timeout/network failures only
    "breaker_threshold": 3,
    "breaker_open_s": 120.0,
}


class SearchServiceError(Exception):
    """Raised when every capable provider failed. Carries the attempts
    summary so callers/UI can show WHY (observability)."""

    def __init__(self, message: str, attempts: list[dict]):
        self.attempts = attempts
        super().__init__(message)


class SearchService:
    def __init__(self, db, config: Optional[dict] = None):
        self.db = db
        self.cache = SearchCache(db)
        self.metrics = SearchMetrics(db)
        merged = dict(DEFAULT_CONFIG)
        merged.update(db.get_setting("search_config", {}) or {})
        merged.update(config or {})
        self.config = merged
        self._health: dict[str, dict] = {}
        self._last_request_at: dict[str, float] = {}
        self._lock = threading.Lock()

    # ---------------- provider chain ----------------
    def _providers(self, request: SearchRequest) -> list:
        """Ordered providers able to serve this request type. 'auto':
        DDG first (zero infra), then SearXNG only when the user actually
        configured a base URL."""
        searxng_base = (self.config.get("searxng_base_url") or "").strip()
        chain: list = [DDGHTMLProvider()]
        if searxng_base:
            chain.append(SearXNGProvider(base_url=searxng_base))
        primary = self.config.get("primary", "auto")
        if primary == "searxng" and searxng_base:
            chain.reverse()
        elif primary not in ("auto", "searxng") and chain[0].name != primary:
            chain = [p for p in chain if p.name == primary] or chain
        return [p for p in chain if p.can_serve(request)]

    # ---------------- health / breaker ----------------
    def _healthy(self, name: str) -> bool:
        h = self._health.get(name)
        return not (h and h["open_until"] > time.monotonic())

    def _record_failure(self, name: str, kind: str) -> None:
        with self._lock:
            h = self._health.setdefault(name, {"consecutive": 0, "open_until": 0.0, "last_error": ""})
            h["consecutive"] += 1
            h["last_error"] = f"{kind}"
            if h["consecutive"] >= self.config["breaker_threshold"]:
                h["open_until"] = time.monotonic() + self.config["breaker_open_s"]

    def _record_success(self, name: str) -> None:
        with self._lock:
            self._health[name] = {"consecutive": 0, "open_until": 0.0, "last_error": ""}

    # ---------------- core path ----------------
    async def _provider_call(self, provider, request: SearchRequest) -> ResultBlocks:
        """Throttled, concurrency-bounded single provider attempt."""
        name = provider.name
        async with asyncio.Semaphore(self.config["max_concurrency"]):
            wait = 0.0
            with self._lock:
                last = self._last_request_at.get(name, 0.0)
                min_i = float(self.config["min_interval_s"])
                now = time.monotonic()
                if now - last < min_i:
                    wait = min_i - (now - last)
                self._last_request_at[name] = now + wait
            if wait > 0:
                await asyncio.sleep(wait)
            async with http_client(timeout_s=float(self.config["timeout_s"])) as client:
                return await provider.search(request, client)

    async def _search_async(self, request: SearchRequest) -> SearchResponse:
        started = time.time()
        request_id = new_request_id()

        # cache-first: repeated ICP queries must never re-hit the engine
        cached = self.cache.get(request.cache_key(), float(self.config["cache_ttl_s"]))
        if cached is not None:
            cached["request_id"] = request_id
            cached["meta"]["cached"] = True
            resp = SearchResponse(**cached)
            self.metrics.record(request_id, request.query, request.page,
                                resp.provider, "cached", resp.meta.latency_ms,
                                resp.meta.total_returned)
            return resp

        attempts: list[dict] = []
        for provider in self._providers(request):
            name = provider.name
            if not self._healthy(name):
                attempts.append({"provider": name, "skipped": "circuit breaker open"})
                continue
            # retries: only transient kinds; blocked/unavailable fall through
            for attempt in range(1, self.config["retries"] + 1):
                try:
                    blocks = await self._provider_call(provider, request)
                    self._record_success(name)
                    latency = int((time.time() - started) * 1000)
                    total = (len(blocks.organic) + len(blocks.news) + len(blocks.images)
                             + len(blocks.places))
                    warnings = []
                    caps: ProviderCapabilities = provider.capabilities
                    if request.type == "news" and not caps.news:
                        warnings.append("provider does not support news")
                    if request.type == "images" and not caps.images:
                        warnings.append("provider does not support images")
                    resp = SearchResponse(
                        request_id=request_id, query=request.query, page=request.page,
                        provider=name, results=blocks,
                        meta={"total_returned": total, "latency_ms": latency, "cached": False},
                        warnings=warnings,
                    )
                    self.cache.put(request.cache_key(),
                                   json.dumps(resp.model_dump(), ensure_ascii=False))
                    self.metrics.record(request_id, request.query, request.page,
                                        name, "ok", latency, total)
                    return resp
                except ProviderFailure as e:
                    attempts.append({"provider": name, "attempt": attempt, "kind": e.kind})
                    self._record_failure(name, e.kind)
                    if e.kind in ("timeout", "network"):
                        await asyncio.sleep(min(2 ** attempt, 10))
                        continue  # transient: retry same provider
                    break        # blocked/unavailable: next provider
            # breaker tripped mid-chain?
            if not self._healthy(name):
                attempts.append({"provider": name, "skipped": "circuit breaker opened"})

        latency = int((time.time() - started) * 1000)
        self.metrics.record(request_id, request.query, request.page,
                            "none", "failed", latency, 0,
                            error="; ".join(f"{a['provider']}:{a.get('kind', a.get('skipped'))}"
                                            for a in attempts))
        raise SearchServiceError(
            "كل مزودات البحث فشلت - جرب تاني بعد شوية أو عدّل إعدادات البحث",
            attempts,
        )

    def search(self, request: SearchRequest) -> SearchResponse:
        """Sync entry point for in-process callers (Qt threads)."""
        return asyncio.run(self._search_async(request))

    async def search_async(self, request: SearchRequest) -> SearchResponse:
        """Async entry point for callers ALREADY inside an event loop
        (FastAPI endpoints)."""
        return await self._search_async(request)

    async def _search_many_async(self, requests: list[SearchRequest]) -> list:
        return await asyncio.gather(*(self._search_async(r) for r in requests),
                                    return_exceptions=True)

    def search_many(self, requests: list[SearchRequest]) -> list:
        """Concurrent searches in ONE loop; exceptions returned inline
        (SearchServiceError instances), never aborting the batch."""
        results = asyncio.run(self._search_many_async(requests))
        return [r if not isinstance(r, Exception) else r for r in results]

    # ---------------- handoff helpers ----------------
    def select_target_urls(self, responses: list[SearchResponse],
                           limit: int = 20, per_domain: int = 1) -> list[str]:
        """Search -> crawler handoff: dedup URLs (and domains by default),
        strip junk, keep ranking order, cap to `limit`. Reuses the SSRF
        guard so a search result can never steer the crawler at private
        network targets."""
        out: list[str] = []
        domains: set[str] = set()
        for resp in responses:
            for r in resp.results.organic:
                if not urls_mod.is_safe_outbound_url(r.url):
                    continue
                d = urls_mod.domain_of(r.url)
                if per_domain and d in domains:
                    continue
                domains.add(d)
                out.append(r.url)
                if len(out) >= limit:
                    return out
        return out

    def health_report(self) -> dict:
        return {
            "providers": [
                {"provider": name, **h, "open": h["open_until"] > time.monotonic()}
                for name, h in self._health.items()
            ],
            "metrics": self.metrics.snapshot(),
        }
