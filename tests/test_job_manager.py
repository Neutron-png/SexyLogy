"""Regression tests for the '9 minutes with zero leads' run (jobs 12-14):
a hard-blocked host (yelp/yellowpages 403 walls + the thumbtack AWS-WAF
shell) used to keep the job zombie-alive with 30s-sleep defer cycles per
URL while producing nothing. The fixes:
  1. HOST-level deferral cap: drops the blocked host's remaining queued
     URLs and ends the job early instead of cycling.
  2. Honest WARNING for 0-record pages instead of a green SUCCESS-0.
  3. WAF rescue: a shell page is re-fetched once with the stealth browser
     waiting on the source's container selector (thumbtack's AWS WAF
     challenge then self-solves and the real listing page arrives), the
     host is remembered straight-to-browser for the rest of the job, and
     a failed rescue marks the host dead so browser runs aren't wasted."""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.engine import fetch_engine as engine
from app.core.engine import anonymity
from app.core.engine.fetch_engine import FetchError
from app.core.job_manager import ScrapeJobWorker
from app.core.models import FetcherMode, ScrapeOptions, TargetConfig
from app.core.storage.db import Database

SHELL_HTML = '<html><head><title></title></head><body><div id="challenge-container"></div></body></html>'
REAL_HTML = ('<html><head><title>The Best Pool Installation in Austin, TX</title></head>'
             '<body><div class="bb b-gray-300 pv3 m_pv4">card</div>'
             + "x" * 6000 + "</body></html>")


def _make_worker(tmp_dir: Path, urls: list[str], mode=FetcherMode.FAST_HTTP) -> ScrapeJobWorker:
    db = Database(tmp_dir / "test.db")
    target = TargetConfig(start_urls=list(urls), max_pages=50, max_depth=0)
    options = ScrapeOptions(
        fetcher_mode=mode,
        timeout_s=5,
        retries=0,
        delay_ms=0,
        use_identity_memory=True,
        use_response_cache=False,
    )
    job_id = db.create_job(None, pages_total=50)
    return ScrapeJobWorker(db, job_id=job_id, project_id=None, target=target,
                           fields=[], options=options)


def _run_worker(worker: ScrapeJobWorker) -> list[str]:
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


def _fake_fetch_403(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
    raise FetchError(url, "HTTP 403")


def test_blocked_host_urls_dropped_and_job_ends():
    """All URLs on one 403-walled host: the job must end quickly with
    the remaining URLs dropped, not cycle 30s defers per URL."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = [f"https://www.yelp.com/search?p={i}" for i in range(6)]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None  # no real waiting
        # no Tor escape hatch on this machine -> pure drop-path regression
        with mock.patch.object(anonymity, "tor_reachable", return_value=False):
            with mock.patch.object(engine, "fetch_one", side_effect=_fake_fetch_403):
                messages = _run_worker(worker)

    joined = "\n".join(messages)
    assert "محجوب" in joined
    assert "تخطي" in joined
    assert "إنهاء المهمة مبكرا" in joined
    assert not any("تم الجلب" in m for m in messages)  # nothing ever fetched


def test_parked_host_is_skipped_without_fetch():
    """A host the brain already parked (gate=False) has its queued URLs
    dropped after 2 deferrals - the fetcher is never hammered."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = [f"https://www.yellowpages.com/search?term=x&page={i}" for i in range(5)]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker._setup_brain = lambda: None
        worker._brain = mock.MagicMock()
        worker._brain.gate.return_value = False
        worker._brain.delay_s.return_value = 0.0
        worker._brain.cache_get.return_value = None
        worker._prepare_proxy = lambda url: None

        fetch_calls: list[str] = []
        with mock.patch.object(anonymity, "tor_reachable", return_value=False):
            with mock.patch.object(engine, "fetch_one",
                                   side_effect=lambda url, *a, **k: fetch_calls.append(url)
                                   or (_ for _ in ()).throw(FetchError(url, "HTTP 403"))):
                messages = _run_worker(worker)

    assert len(fetch_calls) <= 1  # parked URLs never reach the fetcher
    assert any("تخطي" in m and "رابط متبقٍ" in m for m in messages)
    assert any("إنهاء المهمة مبكرا" in m for m in messages)


def test_403_on_fast_lane_hands_host_to_stealth_browser():
    """The yellowpages 403 session: the fast lane's HTTP 403 (the yellow
    WARNING lines) used to end in a red error + a parked host + minutes of
    30s defer cycles. Now the host is handed to scrapling's stealth
    browser (which passes those walls) and its remaining URLs - including
    the one that just got 403'd - go through the browser lane."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = ["https://www.yellowpages.com/search?term=a&page=1",
                "https://www.yellowpages.com/search?term=a&page=2"]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker._setup_brain = lambda: None
        worker._brain = mock.MagicMock()
        worker._brain.gate.return_value = True
        worker._brain.delay_s.return_value = 0.0
        worker._prepare_proxy = lambda url: None
        worker.container = {"selector": "div.result", "type": "css"}
        worker._base_container = worker.container

        calls: list[tuple[str, FetcherMode]] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):  # robots probe - not a job fetch
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            calls.append((url, options.fetcher_mode))
            if options.fetcher_mode == FetcherMode.STEALTH_BROWSER:
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            raise FetchError(url, "HTTP 403")

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_records",
                            side_effect=lambda page, *a, **k:
                                [{"business_name": "Dreemer"}] if page.html == REAL_HTML else []):
                messages = _run_worker(worker)
        finally:
            worker.db.close()

    modes = [m for _, m in calls]
    # URL1: one fast 403 -> straight to stealth (no repeated fast retries);
    # URL2: skips the fast lane entirely (host remembered straight-to-browser)
    assert modes == [FetcherMode.FAST_HTTP, FetcherMode.STEALTH_BROWSER,
                     FetcherMode.STEALTH_BROWSER], modes
    joined = "\n".join(messages)
    assert "متصفح stealth" in joined
    assert joined.count("سجل تم استخراجه") == 2


def test_parked_host_defer_no_longer_stalls_other_hosts():
    """Old behavior: every deferral of a parked host slept 30s INLINE,
    stalling the whole mixed queue behind it while producing nothing.
    Now the parked URL requeues at the END of the queue, and the sleep
    only happens when nothing else is fetchable (single-host park)."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = ["https://www.yellowpages.com/search?term=a",
                "https://www.yelp.com/search?find_desc=a",
                "https://www.yelp.com/search?find_desc=b"]
        worker = _make_worker(Path(tmp), urls)
        sleeps: list[float] = []
        worker._interruptible_sleep = lambda seconds: sleeps.append(seconds)
        worker._setup_brain = lambda: None
        worker._brain = mock.MagicMock()
        worker._brain.gate.side_effect = lambda u: "yellowpages" not in u
        worker._brain.delay_s.return_value = 0.0
        worker._prepare_proxy = lambda url: None
        # stealth already had its one shot for this host -> the gate branch
        # is the genuine defer path (escalation impossible)
        worker._stealth_escalated_hosts.add("www.yellowpages.com")

        fetched: list[tuple[str, int]] = []
        big = "<html><head><title>ok</title></head><body>" + "x" * 9000 + "</body></html>"

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):  # robots probe - not a job fetch
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            fetched.append((url, sum(1 for s in sleeps if s >= 30)))
            return mock.MagicMock(status=200, ok=True, page=_FakePage(big))

        with mock.patch.object(anonymity, "tor_reachable", return_value=False):
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_fields",
                            side_effect=lambda page, fields: {"business_name": "X"}):
                messages = _run_worker(worker)

    joined = "\n".join(messages)
    # the other host kept flowing BEFORE any park sleep was paid
    yelp_fetches = [(u, s) for u, s in fetched if "yelp.com" in u]
    assert len(yelp_fetches) == 2 and all(s == 0 for _, s in yelp_fetches), fetched
    park_sleeps = [s for s in sleeps if s >= 30]
    assert len(park_sleeps) <= 1, sleeps  # at most the single-host-park wait
    assert "تأجيل" in joined         # the parked URL was still deferred...
    assert "تخطي" in joined          # ...then dropped by the host cap


def test_page_is_shell_detects_challenge_page():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://www.thumbtack.com/x/"])
        try:
            assert worker._page_is_shell(_FakePage(SHELL_HTML))     # empty title
            assert worker._page_is_shell(_FakePage(""))             # no html at all
            assert worker._page_is_shell(_FakePage("x" * 100))      # absurdly tiny
            real = ('<html><head><title>Pool Builders in Austin</title></head><body>'
                    + "x" * 20000 + "</body></html>")
            assert not worker._page_is_shell(_FakePage(real))
            small_real = ('<html><head><title>A real page</title></head><body>'
                          + "x" * 3000 + "</body></html>")
            assert not worker._page_is_shell(_FakePage(small_real))  # titled = not shell
        finally:
            worker.db.close()


def test_waf_shell_rescued_by_stealth_and_host_remembered():
    """First thumbtack URL: fast fetch = shell -> stealth rescue with the
    container wait selector returns the real page. Second URL of the same
    host skips the wasted fast fetch and goes straight to stealth."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = ["https://www.thumbtack.com/ny/new-york/pool-builders/",
                "https://www.thumbtack.com/ny/new-york/solar-installers/"]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker.container = {"selector": "div.bb.b-gray-300.pv3.m_pv4", "type": "css"}
        worker._base_container = worker.container

        calls: list[dict] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):  # robots probe - not a job fetch
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            calls.append({"url": url, "mode": options.fetcher_mode,
                          "wait": wait_selector, "timeout": options.timeout_s})
            if options.fetcher_mode == FetcherMode.STEALTH_BROWSER:
                assert wait_selector == "div.bb.b-gray-300.pv3.m_pv4"
                assert options.timeout_s >= 90
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            return mock.MagicMock(status=202, ok=True, page=_FakePage(SHELL_HTML))

        biz_counter = {"n": 0}

        def fake_extract(page, *a, **k):
            if page.html == REAL_HTML:
                biz_counter["n"] += 1
                return [{"business_name": f"Dreemer {biz_counter['n']}"}]
            return []

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_records", side_effect=fake_extract):
                messages = _run_worker(worker)
        finally:
            worker.db.close()

    modes = [c["mode"] for c in calls]
    # fast shell -> stealth rescue -> straight-to-stealth (no second fast try)
    assert modes == [FetcherMode.FAST_HTTP, FetcherMode.STEALTH_BROWSER,
                     FetcherMode.STEALTH_BROWSER]
    assert any("stealth" in m for m in messages)
    joined = "\n".join(messages)
    # one record line per URL (1 each) + final summary "2 سجل ناجح"
    assert joined.count("سجل تم استخراجه") == 2
    assert "2 سجل ناجح" in joined


def test_waf_rescue_failure_marks_host_dead():
    """When the stealth rescue also returns a shell, the host is marked
    dead: later URLs fail fast (one fast attempt, no browser retries)."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = ["https://www.thumbtack.com/ny/new-york/pool-builders/",
                "https://www.thumbtack.com/ny/new-york/solar-installers/"]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker.container = {"selector": "div.pro-card", "type": "css"}
        worker._base_container = worker.container

        calls: list[FetcherMode] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):  # robots probe - not a job fetch
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            calls.append(options.fetcher_mode)
            return mock.MagicMock(status=202, ok=True, page=_FakePage(SHELL_HTML))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_records", return_value=[]):
                messages = _run_worker(worker)
        finally:
            worker.db.close()

    # one fast + one stealth rescue for URL1, then ONLY fast for URL2
    assert calls == [FetcherMode.FAST_HTTP, FetcherMode.STEALTH_BROWSER,
                     FetcherMode.FAST_HTTP]
    joined = "\n".join(messages)
    assert "shell فاضي" in joined and "محمي ضد الجلب الآلي" in joined
    assert "0 سجل تم استخراجه" not in joined  # never a green SUCCESS-0


def test_progress_denominator_is_known_work_not_max_pages():
    """The UI loading bar sat at 0% for whole jobs: the progress
    denominator was target.max_pages (up to 100,000) instead of the
    actual known work. Every emitted total must now be bounded by the
    real queue (done + remaining), so done/total actually moves."""
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), [
            "https://www.yellowpages.com/search?term=a",
            "https://www.yellowpages.com/search?term=b",
        ])
        worker._interruptible_sleep = lambda seconds: None
        worker._setup_brain = lambda: None
        worker._brain = mock.MagicMock()
        worker._brain.gate.return_value = True
        worker._brain.delay_s.return_value = 0.0
        worker._prepare_proxy = lambda url: None

        progress: list[tuple[int, int]] = []
        worker.progress.connect(lambda d, t, ok, fail: progress.append((d, t)))

        big = "<html><head><title>ok</title></head><body>" + "x" * 9000 + "</body></html>"

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            return mock.MagicMock(status=200, ok=True, page=_FakePage(big))

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch), \
                 mock.patch("app.core.job_manager.extract_fields",
                            side_effect=lambda page, fields: {"business_name": "X"}):
                worker.run()
        finally:
            worker.db.close()

    assert progress, "worker never emitted progress"
    totals = [t for _, t in progress]
    assert max(totals) <= 2, totals  # the old code emitted 100,000 here
    assert progress[-1][0] == 2      # both pages accounted for by the end


def test_browser_options_are_stealth():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://x.com/"])
        try:
            opts = worker._browser_options()
            assert opts.fetcher_mode == FetcherMode.STEALTH_BROWSER
            assert opts.retries == 0
            assert opts.use_response_cache is False
            assert opts.timeout_s >= 90
            assert worker._container_wait_selector() is None
            worker.container = {"selector": "div.a", "type": "css"}
            assert worker._container_wait_selector() == "div.a"
        finally:
            worker.db.close()


def test_burst_pacer_gap_capped_by_user_delay():
    """Found by timing runs: BurstPacer's quiet-regime mean is ~20s by
    design, silently turning a 2000ms user delay into a ~30s-per-request
    crawl. The gap must be capped relative to the user's own delay."""
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(Path(tmp), ["https://www.yellowpages.com/x/"])
        worker._setup_brain = lambda: None
        worker._brain = mock.MagicMock()
        worker._brain.delay_s.return_value = 0.0
        worker._pacer = mock.MagicMock()
        worker._pacer.sample.return_value = 999.0  # pacer wants 16 minutes
        sleeps: list[float] = []
        worker._interruptible_sleep = lambda seconds: sleeps.append(seconds)
        try:
            owned = worker._pacing_sleep("https://www.yellowpages.com/x/")
        finally:
            worker.db.close()
    assert owned is True
    assert sleeps and sleeps[0] <= 3.0 * (2000 / 1000.0) + 1.0  # 2000ms user delay → cap 7s


def test_per_source_fetcher_routing_yelp_stealth_yp_fast():
    """Source profiles can declare their own engine: yelp runs stealth,
    yellowpages keeps the fast lane - in the SAME job."""
    with tempfile.TemporaryDirectory() as tmp:
        urls = ["https://www.yellowpages.com/search?term=x",
                "https://www.yelp.com/search?find_desc=x"]
        worker = _make_worker(Path(tmp), urls)
        worker._interruptible_sleep = lambda seconds: None
        worker.source_profiles = [
            {"name": "yellowpages", "domain": "yellowpages.com",
             "container": {"selector": "div.result", "type": "css"}, "fields": []},
            {"name": "yelp", "domain": "yelp.com",
             "container": {"selector": "div.hoverable", "type": "css"}, "fields": [],
             "fetcher_mode": FetcherMode.STEALTH_BROWSER},
        ]
        modes: list[str] = []

        def fake_fetch(url, options, should_stop=None, cache=None, wait_selector=None, session=None):
            if url.endswith("/robots.txt"):  # robots probe - not a job fetch
                return mock.MagicMock(status=200, ok=True, page=_FakePage(REAL_HTML))
            modes.append(options.fetcher_mode.value)
            big = "<html><head><title>ok</title></head><body>" + "x" * 9000 + "</body></html>"
            page = _FakePage(big)
            page.css = lambda *a, **k: []  # empty listing rows
            return mock.MagicMock(status=200, ok=True, page=page)

        try:
            with mock.patch.object(engine, "fetch_one", side_effect=fake_fetch):
                messages = _run_worker(worker)
        finally:
            worker.db.close()

    assert modes == ["fast_http", "stealth"], modes
    joined = "\n".join(messages)
    assert joined.count("تم الجلب، جاري الاستخراج") == 2
