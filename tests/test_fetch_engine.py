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
