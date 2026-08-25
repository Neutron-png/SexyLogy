"""
SQLite storage layer. stdlib-only (sqlite3), so it runs anywhere Python
runs and is fully unit-testable without Qt or Scrapling.

Stores: projects, job runs (history), job results (row-per-record, kept
out of the main app.db logic path for large jobs - see results table
comment below), settings, templates, logs.

Secrets (proxy credentials) are never stored in plain columns next to the
rest of a project's config; see `secrets.py` for the encrypted store.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_run_at REAL,
    last_result_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    pages_done INTEGER NOT NULL DEFAULT 0,
    pages_total INTEGER NOT NULL DEFAULT 0,
    records_ok INTEGER NOT NULL DEFAULT 0,
    records_failed INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

-- Results are kept in a dedicated table (and can be moved to a per-job
-- file store, see storage/results_store.py) so a huge scrape never
-- bloats the row size of the `jobs` table the dashboard queries.
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    source_url TEXT,
    data_json TEXT NOT NULL,
    scraped_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_results_job ON results(job_id);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_job ON logs(job_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL,
    builtin INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    """
    IMPORTANT - thread safety: one Database instance is shared between the
    Qt GUI thread and the background QThread that runs a scrape job
    (app/core/job_manager.py writes logs/results/progress from that
    worker thread while the GUI thread reads for the live view). sqlite3
    connections default to check_same_thread=True, which raises
    ProgrammingError the moment a second thread touches them.

    That was a real, previously-shipped bug here: every single
    self.db.add_log()/add_result()/update_job_progress() call from inside
    ScrapeJobWorker.run() raised immediately and uncaught (it happened
    before run()'s own try/except), which silently killed the worker
    thread on its very first log line. Symptom on screen: Stop button did
    nothing (the thread that was supposed to check _stop_requested had
    already died), the progress bar never moved, and the log panel never
    showed a single line - exactly the three symptoms reported. The
    thread never got a chance to run() -> finished.emit(), so it also
    never reached thread.quit(), leaving Qt showing "RUNNING" forever.

    Fix: open the connection with check_same_thread=False (multiple
    threads may use it) and serialize every access with an RLock (a bare
    sqlite3.Connection is not safe for concurrent use from two threads at
    once, even though a single thread revisiting it - e.g. a nested
    cursor() call - is fine, hence RLock not Lock).
    """

    def __init__(self, path: str | Path = "logy.db"):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    # ---------------- Projects ----------------
    def create_project(self, name: str, config: dict) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO projects (name, config_json, created_at) VALUES (?, ?, ?)",
                (name, json.dumps(config), time.time()),
            )
            return cur.lastrowid

    def update_project(self, project_id: int, name: str, config: dict) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE projects SET name = ?, config_json = ? WHERE id = ?",
                (name, json.dumps(config), project_id),
            )

    def touch_project_run(self, project_id: int, result_count: int) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE projects SET last_run_at = ?, last_result_count = ? WHERE id = ?",
                (time.time(), result_count, project_id),
            )

    def delete_project(self, project_id: int) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def list_projects(self) -> list[dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
            return [dict(r) for r in cur.fetchall()]

    def get_project(self, project_id: int) -> Optional[dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # ---------------- Jobs ----------------
    def create_job(self, project_id: Optional[int], pages_total: int = 0) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO jobs (project_id, status, started_at, pages_total) VALUES (?, 'running', ?, ?)",
                (project_id, time.time(), pages_total),
            )
            return cur.lastrowid

    def update_job_progress(self, job_id: int, pages_done: int, records_ok: int, records_failed: int) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET pages_done = ?, records_ok = ?, records_failed = ? WHERE id = ?",
                (pages_done, records_ok, records_failed, job_id),
            )

    def finish_job(self, job_id: int, status: str, error: Optional[str] = None) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE jobs SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (status, time.time(), error, job_id),
            )

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in cur.fetchall()]

    def get_job(self, job_id: int) -> Optional[dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    # ---------------- Results (streamed in as a job runs) ----------------
    def add_result(self, job_id: int, source_url: str, data: dict) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO results (job_id, source_url, data_json, scraped_at) VALUES (?, ?, ?, ?)",
                (job_id, source_url, json.dumps(data, ensure_ascii=False), time.time()),
            )
            return cur.lastrowid

    def count_results(self, job_id: int) -> int:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM results WHERE job_id = ?", (job_id,))
            return cur.fetchone()[0]

    def page_results(self, job_id: int, offset: int, limit: int) -> list[dict]:
        """Windowed read used by the virtualized results table - never
        loads the full result set into memory at once."""
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM results WHERE job_id = ? ORDER BY id LIMIT ? OFFSET ?",
                (job_id, limit, offset),
            )
            return [dict(r) for r in cur.fetchall()]

    def iter_all_results(self, job_id: int, batch_size: int = 500) -> Iterator[dict]:
        """Streaming export helper - reads in batches instead of one giant query."""
        offset = 0
        while True:
            batch = self.page_results(job_id, offset, batch_size)
            if not batch:
                return
            yield from batch
            offset += batch_size

    # ---------------- Logs ----------------
    def add_log(self, job_id: Optional[int], level: str, message: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO logs (job_id, level, message, ts) VALUES (?, ?, ?, ?)",
                (job_id, level, message, time.time()),
            )

    def list_logs(self, job_id: Optional[int] = None, level: Optional[str] = None, limit: int = 1000) -> list[dict]:
        query = "SELECT * FROM logs WHERE 1=1"
        params: list[Any] = []
        if job_id is not None:
            query += " AND job_id = ?"
            params.append(job_id)
        if level:
            query += " AND level = ?"
            params.append(level)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def clear_logs(self) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM logs")

    # ---------------- Settings ----------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    # ---------------- Templates ----------------
    def create_template(self, name: str, config: dict, builtin: bool = False) -> int:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO templates (name, config_json, builtin) VALUES (?, ?, ?)",
                (name, json.dumps(config), int(builtin)),
            )
            return cur.lastrowid

    def list_templates(self) -> list[dict]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM templates ORDER BY builtin DESC, id ASC")
            return [dict(r) for r in cur.fetchall()]

    def update_template_config(self, name: str, config: dict) -> None:
        """Overwrite a builtin template's config in place. Used by
        seed_builtin_templates() to upgrade previously-seeded builtin
        templates (e.g. the ICP niche templates) when the app ships new
        selectors/URLs for them. Scoped to builtin=1 rows only, so a
        user's own saved templates are never touched even if they reuse
        a builtin name."""
        with self.cursor() as cur:
            cur.execute(
                "UPDATE templates SET config_json = ? WHERE name = ? AND builtin = 1",
                (json.dumps(config), name),
            )
