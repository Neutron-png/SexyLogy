"""Tests for the engineering-audit fixes (C1-C3, H3-H6, M-items):
- WAL journal mode + use-after-close guard on Database
- proxy credentials: encrypted project storage, hashed identity keys,
  central log redaction
- per-(identity, domain) reputation pairs in the Brain
- bounded response cache
- AIMD decay on load
- INTERRUPTED sweep + job_queue checkpoint/resume
- robots.txt enforcement
- source-health consecutive-zero detection
- AI call budget + provenance marker
- extract_links scheme guard"""
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.engine import fetch_engine as engine
from app.core.engine import ai_extractor
from app.core.engine.brain import Brain
from app.core.engine.fetch_engine import FetchError
from app.core.job_manager import JobManager, ScrapeJobWorker
from app.core.models import FetcherMode, ScrapeOptions, TargetConfig
from app.core.storage.db import Database
from app.core.storage.secrets import (
    SecretStore, encrypt_proxy_list, decrypt_proxy_list, redact_secrets,
)

REAL_HTML = ('<html><head><title>Austin Pool Builders</title></head><body>'
             + "x" * 9000 + "</body></html>")


def _make_worker(tmp_dir: Path, urls, **kw) -> ScrapeJobWorker:
    db = Database(tmp_dir / "test.db")
    target = TargetConfig(start_urls=list(urls), max_pages=50, max_depth=0)
    options = ScrapeOptions(fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=5,
                            retries=0, delay_ms=0, use_identity_memory=True,
                            use_response_cache=False)
    job_id = db.create_job(None, pages_total=len(urls))
    return ScrapeJobWorker(db, job_id=job_id, project_id=None, target=target,
                           fields=[], options=options, **kw)


def _run(worker: ScrapeJobWorker) -> list[str]:
    messages: list[str] = []
    worker.log.connect(lambda level, msg: messages.append(msg))
    try:
        worker.run()
    finally:
        worker.db.close()
    return messages


class _FakePage:
    def __init__(self, html: str):
        self.html = html
        self.status = 200


# ---------------------------------------------------------------- database

def test_wal_mode_and_busy_timeout_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "w.db")
        try:
            mode = db._conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert str(mode).lower() == "wal"
            bt = db._conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert int(bt) >= 5000
        finally:
            db.close()


def test_database_rejects_use_after_close():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "c.db")
        db.close()
        try:
            db.count_results(1)
            assert False, "expected RuntimeError after close"
        except RuntimeError as e:
            assert "مغلقة" in str(e)


# ---------------------------------------------------------------- secrets

def test_proxy_list_encryption_roundtrip():
    proxies = ["socks5://user:s3cret@1.2.3.4:1080", "http://alice:pw@proxy.io:8080"]
    blob = encrypt_proxy_list(proxies)
    assert "s3cret" not in blob and "1.2.3.4" not in blob  # ciphertext only
    assert decrypt_proxy_list(blob) == proxies


def test_redact_secrets_scrubs_credentials():
    line = "fetch via http://user:secretpw@proxy.example.com:8080 done"
    out = redact_secrets(line)
    assert "secretpw" not in out
    assert "***:***@proxy.example.com:8080" in out


def test_proxy_credentials_never_reach_identity_stats():
    """Audit C2: the brain used to persist the RAW proxy URL (with
    credentials) as identity_stats.key. It must only ever see the alias."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "k.db")
        job_id = db.create_job(None, pages_total=1)
        target = TargetConfig(start_urls=["https://www.yelp.com/x"], max_pages=1, max_depth=0)
        options = ScrapeOptions(fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=5,
                                retries=0, delay_ms=0, use_identity_memory=True,
                                proxy__mode="single") if False else ScrapeOptions(
            fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=5, retries=0,
            delay_ms=0, use_identity_memory=True)
        from app.core.models import ProxyConfig
        options.proxy = ProxyConfig(mode="single", proxies=["socks5://user:topsecret@10.0.0.9:1080"])
        worker = ScrapeJobWorker(db, job_id=job_id, project_id=None, target=target,
                                 fields=[], options=options)
        worker._interruptible_sleep = lambda s: None

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            raise FetchError(url, "HTTP 403")

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                for _ in worker.run():
                    pass
        except TypeError:
            pass  # run() returns None; generator-guard for safety
        keys = [r["key"] for r in db._conn.execute("SELECT key FROM identity_stats")]
        joined = " ".join(keys)
        assert "topsecret" not in joined and "10.0.0.9" not in joined, keys
        assert any(k.startswith("px:") for k in keys), keys
        db.close()


# ---------------------------------------------------------------- brain

def test_brain_pair_reputation_is_per_domain():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "b.db")
        brain = Brain(db)
        for _ in range(3):
            brain.record("p1", "yelp.com", blocked=True)
        brain.record("p1", "yellowpages.com", blocked=False, latency_s=0.1)
        # burned on yelp...
        assert brain.pair_block_prob("p1", "yelp.com") > 0.5
        # ...but yelp's grudge must not follow the proxy to yellowpages
        assert brain.pair_block_prob("p1", "yellowpages.com") < 0.5
        db.close()


def test_brain_cache_bounded():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "cache.db")
        brain = Brain(db)
        brain.CACHE_PRUNE_CHECK_EVERY = 1
        brain.CACHE_MAX_ROWS = 4
        brain.CACHE_PRUNE_TO = 2
        big = "x" * (brain.CACHE_MAX_BODY_BYTES + 1)
        brain.cache_put("https://big.example/", big)          # oversized: skipped
        assert brain.cache_get("https://big.example/", 10**9) is None
        for i in range(6):
            brain.cache_put(f"https://s.example/{i}", "small page")
        n = db._conn.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0]
        assert n <= 4, n
        db.close()


def test_brain_aimd_decay_on_load():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "aimd.db"
        db = Database(path)
        brain = Brain(db)
        for _ in range(6):  # 1500ms * 2^6 -> capped at 30s
            brain.record("direct", "www.yelp.com", blocked=True)
        assert brain.delay_s("www.yelp.com") >= 30.0
        db.close()
        db2 = Database(path)
        brain2 = Brain(db2)
        # a dead process's grudge must not open the new job at 30s/request
        assert brain2.delay_s("www.yelp.com") <= brain2.AIMD_START_MS * 4 / 1000.0
        db2.close()


# ---------------------------------------------------------------- recovery

def test_interrupt_sweep_marks_stale_running_jobs():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "s.db")
        stale_id = db.create_job(None, pages_total=5)
        db.add_result(stale_id, "https://x/1", {"business_name": "A"})
        db.add_result(stale_id, "https://x/2", {"business_name": "B"})
        with db.cursor() as cur:  # old heartbeat: process died long ago
            cur.execute("INSERT INTO logs (job_id, level, message, ts) VALUES (?, 'INFO', 'old', ?)",
                        (stale_id, time.time() - 10_000))
        fresh_id = db.create_job(None, pages_total=1)
        db.add_log(fresh_id, "INFO", "just started")  # fresh heartbeat

        marked = db.mark_stale_running_as_interrupted(older_than_s=90.0)

        assert marked == [stale_id]
        assert db.get_job(stale_id)["status"] == "interrupted"
        assert db.get_job(stale_id)["records_ok"] == 2  # recomputed from results
        assert db.get_job(fresh_id)["status"] == "running"
        db.close()


def test_queue_checkpoint_and_resume():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "r.db")
        urls = [f"https://www.yellowpages.com/search?term={i}" for i in range(3)]
        target = TargetConfig(start_urls=list(urls), max_pages=5, max_depth=0)
        options = ScrapeOptions(fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=5,
                                retries=0, delay_ms=0, use_identity_memory=True)
        job_id = db.create_job(None, pages_total=3)
        db.set_job_spec(job_id, JobManager._serialize_spec(target, [], options, None, None, None))
        worker = ScrapeJobWorker(db, job_id=job_id, project_id=None, target=target,
                                 fields=[], options=options)

        fetches: list[str] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            fetches.append(url)
            if len(fetches) == 2:  # app dies mid-job on the 2nd URL
                worker.request_stop()
            return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                worker.run()

            counts = db.queue_counts(job_id)
            assert counts.get("done") == 1 and counts.get("pending") == 1, counts

            # ---- resume: rebuild from spec + pending checkpoint ----
            jm = JobManager(db)
            job_id2, worker2 = jm.prepare_resume_job(job_id)
            assert job_id2 == job_id
            assert worker2.resumed is True
            # the in-flight URL (term=1) was reset to pending and must be
            # part of the resume set - otherwise resume silently loses work
            assert any("term=1" in u for u, _ in db.queue_pending(job_id))
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                worker2.run()
            counts2 = db.queue_counts(job_id)
            assert counts2.get("done") == 3 and counts2.get("failed", 0) == 0, counts2
            # the completed URL is never re-fetched; the interrupted one is
            assert fetches.count(urls[0]) == 1
        finally:
            db.close()


# ---------------------------------------------------------------- behavior

def test_robots_txt_disallow_skips_fetch():
    from urllib import robotparser
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://www.yelp.com/search?q=1"])
        rp = robotparser.RobotFileParser()
        rp.parse(["User-agent: *", "Disallow: /"])
        worker._robots_cache["www.yelp.com"] = rp
        fetches: list[str] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            fetches.append(url)
            return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                messages = _run(worker)
        finally:
            worker.db.close()
    assert not fetches  # nothing fetched at all
    assert any("robots.txt يمنع" in m for m in messages)


def test_source_health_consecutive_zero_pages_escalates():
    with tempfile.TemporaryDirectory() as tmp:
        urls = [f"https://www.yellowpages.com/search?term={i}" for i in range(10)]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker.container = {"selector": "div.result", "type": "css"}
        worker._base_container = worker.container
        worker._robots_cache["www.yellowpages.com"] = None  # robots: fail-open

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_records", return_value=[]):
                messages = _run(worker)
        finally:
            worker.db.close()
    joined = "\n".join(messages)
    assert "المستخرج مش شغال" in joined  # selector-degradation ERROR, not quiet zeros


def test_extract_links_blocks_non_http_schemes():
    class _HrefList(list):
        def getall(self):
            return list(self)

    class _LinksPage:
        def css(self, *a, **k):
            return _HrefList(["https://ok.example/page", "file:///etc/passwd",
                              "javascript:void(0)", "ftp://files.example/x"])

    links = engine.extract_links(_LinksPage(), "https://ok.example/", same_domain_only=False)
    assert links == ["https://ok.example/page"]


# ---------------------------------------------------------------- AI

def test_ai_budget_cap_and_provenance_marker():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://x.com/"])
        from app.core.models import AIExtractionConfig
        worker.options.ai_extraction = AIExtractionConfig(provider="openai", ai_call_budget=1)
        db = worker.db
        from app.core.storage.secrets import SecretStore as _SS
        db.set_setting("api_keys", {"openai": _SS().encrypt("sk-test")})

        calls = {"n": 0}

        def fake_extract(provider, text, fields, key):
            calls["n"] += 1
            return {"owner_name": "Alice"}

        try:
            with mock.patch.object(engine, "fetch_one",
                                   side_effect=lambda url, *a, **k: mock.MagicMock(
                                       status=200, ok=True, page=_FakePage(REAL_HTML))), \
                 mock.patch.object(ai_extractor, "extract", side_effect=fake_extract):
                record = {"website": "https://biz.example/"}
                worker._lookup_owner_contact_info(record, "https://src.example/")
                assert record.get("owner_name") == "Alice"
                assert record.get("_ai_extracted_fields") == ["owner_name"]
                # budget = 1: the second call must be refused WITHOUT paying
                worker._lookup_owner_contact_info({"website": "https://biz2.example/"}, "https://src.example/")
        finally:
            worker.db.close()
    assert calls["n"] == 1


# ---------------------------------------------------------------- sessions + adaptive

def test_persistent_session_lifecycle_per_job():
    """The official Session Support mix: one session per engine per job,
    every fetch routed through it with the brain's per-request proxy, and
    a guaranteed close when the job ends."""
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), [
            "https://www.yellowpages.com/search?term=a",
            "https://www.yellowpages.com/search?term=b",
        ])
        worker._robots_cache["www.yellowpages.com"] = None

        class FakeHandle:
            def __init__(self):
                self.entered = False
                self.closed = False
                self.fetch_calls: list[str] = []

            def enter(self):
                self.entered = True

            def fetch(self, url, options, wait_selector=None, proxy_override=None):
                self.fetch_calls.append(url)
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))

            def close(self):
                self.closed = True

        handle = FakeHandle()
        proxies_seen: list = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            proxies_seen.append(session)
            return session.fetch(url, options)

        try:
            with mock.patch.object(engine, "open_session", return_value=handle), \
                 mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                messages = _run(worker)
        finally:
            worker.db.close()

    assert handle.entered and handle.closed  # lifecycle: opened once, closed once
    assert handle.fetch_calls == [
        "https://www.yellowpages.com/search?term=a",
        "https://www.yellowpages.com/search?term=b",
    ]
    assert all(p is handle for p in proxies_seen)
    assert "2 سجل ناجح" in "\n".join(messages)


def test_adaptive_relocation_rescues_zero_record_pages():
    """Selector-rot mix (the fetch engine Adaptive Scraping): first page per host
    saves the container fingerprint (auto_save=True); a zero-record page
    gets one adaptive=True relocation retry before counting as empty."""
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://www.yellowpages.com/search?term=a"])
        worker._robots_cache["www.yellowpages.com"] = None
        worker.container = {"selector": "div.result", "type": "css"}
        worker._base_container = worker.container

        calls: list[dict] = []

        def fake_extract(page, *args, **kwargs):
            calls.append(kwargs)
            if kwargs.get("adaptive"):
                return [{"business_name": "Relocated Co", "phone": "1234567890"}]
            return []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_records", side_effect=fake_extract):
                messages = _run(worker)
        finally:
            worker.db.close()

    # first pass: auto_save=True, adaptive=False; second pass: adaptive=True
    assert calls[0].get("auto_save") is True and calls[0].get("adaptive") is False
    assert calls[1].get("adaptive") is True
    assert "1 سجل تم استخراجه" in "\n".join(messages)


def test_engine_diagnostics_shape():
    from app.core.engine.fetch_engine import engine_diagnostics
    diag = engine_diagnostics()
    assert set(diag) >= {"engine", "fetchers_deps", "browsers", "ok", "detail"}
    assert diag["ok"] is True  # this environment completed the official install


# ---------------------------------------------------------------- anti-protection wiring

def test_cookies_reach_fast_http_as_header():
    """the engine's fast HTTP fetcher has no cookies= kwarg - LOGY bakes
    options.cookies into the Cookie header (works on every engine)."""
    from app.core.models import ScrapeOptions as Opts
    opts = Opts(fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=5, retries=0,
                cookies={"session": "abc123"})
    captured = {}

    class _FakeFetcher:
        @staticmethod
        def get(url, **kw):
            captured.update(kw)
            return mock.MagicMock(status=200, html_content=REAL_HTML)

    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(engine, "Fetcher", _FakeFetcher):
            fr = engine.fetch_one("https://x.example/", opts)
    assert fr.ok
    assert captured["headers"]["Cookie"] == "session=abc123"


def test_stealth_fetch_receives_all_protection_kwargs():
    """Every official anti-protection capability must actually reach the
    stealth engine: cookies, persistent profile, real chrome, block_ads,
    DoH, extra_headers, cdp_url."""
    from app.core.models import ScrapeOptions as Opts
    opts = Opts(fetcher_mode=FetcherMode.STEALTH_BROWSER, timeout_s=10,
                cookies={"sid": "xyz"}, block_ads=True, dns_over_https=True,
                real_chrome=True, cdp_url="http://127.0.0.1:9222",
                headers={"X-Test": "1"})

    captured = {}

    class _FakeStealth:
        @staticmethod
        def fetch(url, **kw):
            captured.update(kw)
            return mock.MagicMock(status=200, html_content=REAL_HTML)

    with mock.patch.object(engine, "StealthyFetcher", _FakeStealth):
        fr = engine.fetch_one("https://www.yelp.com/x", opts,
                              wait_selector="div.pro")

    assert captured["cookies"] == {"sid": "xyz"}
    assert captured["extra_headers"]["X-Test"] == "1"
    assert captured["block_ads"] is True
    assert captured["dns_over_https"] is True
    assert captured["real_chrome"] is True
    assert captured["cdp_url"] == "http://127.0.0.1:9222"
    assert captured["wait_selector"] == "div.pro"
    assert "browser_profiles" in (captured.get("user_data_dir") or "")


def test_browser_profile_dir_is_stable_and_persistent():
    from app.core.engine.fetch_engine import _browser_profile_dir
    d1 = _browser_profile_dir(FetcherMode.STEALTH_BROWSER)
    d2 = _browser_profile_dir(FetcherMode.STEALTH_BROWSER)
    assert d1 and d1 == d2  # same dir across calls = cookies survive jobs
    assert "browser_profiles" in d1
    assert Path(d1).exists()
