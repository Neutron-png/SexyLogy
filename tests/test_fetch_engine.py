from types import SimpleNamespace
from unittest.mock import patch

from app.core.engine import fetch_engine
from app.core.models import FetcherMode, ScrapeOptions


def test_fast_http_with_retries_disabled_still_makes_one_request():
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status=200)

    options = ScrapeOptions(
        fetcher_mode=FetcherMode.FAST_HTTP,
        timeout_s=2,
        retries=0,
    )
    fake_fetcher = SimpleNamespace(get=fake_get)

    with patch.object(fetch_engine, "require_engine"), patch.object(
        fetch_engine, "Fetcher", fake_fetcher, create=True,
    ):
        result = fetch_engine.fetch_one("https://example.com", options)

    assert result.ok is True
    assert len(calls) == 1
    assert calls[0][0] == "https://example.com"
    assert calls[0][1]["retries"] == 1


def test_session_fetch_bad_status_raises_like_non_session_path():
    """BUG-002 regression: a session-path fetch that landed a 403/429
    interstitial used to be returned as a SUCCESSFUL fetch (ok=False
    result passed straight through fetch_one) - the job manager logged
    'تم الجلب' on a blocked page and fed a SUCCESS into the identity
    brain instead of a block signal."""
    from app.core.engine.fetch_engine import FetchError, FetchResult

    class _FakeSession:
        def fetch(self, url, options, wait_selector=None, proxy_override=None):
            return FetchResult(url=url, page=object(), status=403, ok=False)

    options = ScrapeOptions(fetcher_mode=FetcherMode.STEALTH_BROWSER, timeout_s=2)
    with patch.object(fetch_engine, "require_engine"):
        try:
            fetch_engine.fetch_one("https://example.com", options, session=_FakeSession())
        except FetchError as e:
            assert "HTTP 403" in str(e)
        else:
            raise AssertionError("session fetch with HTTP 403 did not raise FetchError")


def test_browser_session_proxy_override_skipped_for_persistent_context():
    """BUG-001 regression: passing a per-fetch proxy override to a
    persistent-context browser session (browser=None) makes scrapling
    raise 'Browser not initialized for proxy rotation mode' - the exact
    failure that killed every browser-lane page under tor/hybrid (prod
    job #19: 53 errors, 0 records). The override must only be passed
    when the session actually launched a separate browser."""
    handle = fetch_engine._BrowserSessionHandle.__new__(fetch_engine._BrowserSessionHandle)
    captured: dict = {}

    def fake_fetch(url, **kw):
        captured.update(kw)
        return SimpleNamespace(status=200)

    handle._session = SimpleNamespace(fetch=fake_fetch, browser=None)

    handle.fetch("https://example.com", ScrapeOptions(timeout_s=20),
                 proxy_override="socks5://127.0.0.1:9050")
    assert "proxy" not in captured, captured

    handle._session = SimpleNamespace(fetch=fake_fetch, browser=object())
    handle.fetch("https://example.com", ScrapeOptions(timeout_s=20),
                 proxy_override="socks5://127.0.0.1:9050")
    assert captured["proxy"] == "socks5://127.0.0.1:9050"
