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
