from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest import mock

from app.core.search.schema import (
    OrganicResult, ProviderCapabilities, ProviderFailure, SearchRequest,
    SearchResponse, ResultBlocks,
)
from app.core.search.service import SearchService, SearchServiceError


def _resp(provider="ddg_html", n=3, url_base="https://a.com/"):
    blocks = ResultBlocks(organic=[
        OrganicResult(position=i + 1, title=f"T{i}", url=f"{url_base}{i}")
        for i in range(n)
    ])
    return SearchResponse(
        request_id="req_cached", query="q", page=1, provider=provider,
        results=blocks, meta={"total_returned": n, "latency_ms": 10, "cached": False},
    )


class _FakeProvider:
    name = "fake"
    capabilities = ProviderCapabilities(organic=True)

    def __init__(self, fail_kind: str | None = None, n: int = 3):
        self.fail_kind = fail_kind
        self.calls = 0
        self.n = n

    def can_serve(self, request):
        return True

    async def search(self, request, client):
        self.calls += 1
        if self.fail_kind:
            raise ProviderFailure(self.name, self.fail_kind, "boom")
        return _resp(provider=self.name, n=self.n).results


def _svc(tmp_path, **config):
    from app.core.storage.db import Database
    db = Database(tmp_path / "svc.db")
    return SearchService(db, config=config), db


def test_cache_hit_and_miss(tmp_path):
    service, db = _svc(tmp_path)
    fake = _FakeProvider(n=2)
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=fake), \
         mock.patch("app.core.search.service.SearXNGProvider", None), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()):
        req = SearchRequest(query="same query")
        r1 = service.search(req)
        assert fake.calls == 1 and r1.meta.cached is False
        r2 = service.search(req)
        assert fake.calls == 1, "cache miss on identical request!"
        assert r2.meta.cached is True and len(r2.results.organic) == 2


def test_fallback_to_second_provider(tmp_path):
    service, db = _svc(tmp_path, searxng_base_url="http://127.0.0.1:9999")
    broken = _FakeProvider(fail_kind="blocked")
    good = _FakeProvider()
    good.name = "searxng"
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=broken), \
         mock.patch("app.core.search.service.BingHTMLProvider", return_value=_FakeProvider(fail_kind="blocked")), \
         mock.patch("app.core.search.service.SearXNGProvider", return_value=good), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()):
        resp = service.search(SearchRequest(query="q"))
        assert resp.provider == "searxng"
        assert broken.calls == 1 and good.calls == 1


def test_all_providers_fail_raises_structured_error(tmp_path):
    service, db = _svc(tmp_path, searxng_base_url="http://127.0.0.1:9999")
    broken = _FakeProvider(fail_kind="blocked")
    broken2 = _FakeProvider(fail_kind="timeout")
    broken2.name = "searxng"
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=broken), \
         mock.patch("app.core.search.service.BingHTMLProvider", return_value=_FakeProvider(fail_kind="blocked")), \
         mock.patch("app.core.search.service.SearXNGProvider", return_value=broken2), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()), \
         mock.patch("app.core.search.service.asyncio.sleep", mock.AsyncMock()):
        try:
            service.search(SearchRequest(query="q"))
        except SearchServiceError as e:
            assert len(e.attempts) >= 2
        else:
            raise AssertionError("no error when all providers fail")


def test_circuit_breaker_opens_and_skips(tmp_path):
    service, db = _svc(tmp_path, searxng_base_url="http://127.0.0.1:9999")
    broken = _FakeProvider(fail_kind="blocked")
    bing_broken = _FakeProvider(fail_kind="blocked")
    bing_broken.name = "bing_html"   # distinct name - health is keyed by provider name
    good = _FakeProvider()
    good.name = "searxng"
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=broken), \
         mock.patch("app.core.search.service.BingHTMLProvider", return_value=bing_broken), \
         mock.patch("app.core.search.service.SearXNGProvider", return_value=good), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()):
        for _ in range(3):  # threshold=3 -> breaker opens
            service.search(SearchRequest(query=f"q{broken.calls}-{good.calls}"))
            # reset 'good' result ids irrelevant; each search distinct query
        assert service._health["fake"]["consecutive"] >= 3
        # 4th search: broken must be SKIPPED (breaker open) -> only good called again
        before_good = good.calls
        r = service.search(SearchRequest(query="after breaker"))
        assert r.provider == "searxng"
        assert broken.calls == 3, "blocked provider was hammered despite breaker"
        assert good.calls == before_good + 1


def test_transient_failure_retries_then_succeeds(tmp_path):
    service, db = _svc(tmp_path, retries=2)
    flaky = _FakeProvider()

    calls = {"n": 0}

    async def flaky_search(request, client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderFailure(flaky.name, "timeout", "slow")
        return _resp(provider=flaky.name, n=1).results

    flaky.search = flaky_search
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=flaky), \
         mock.patch("app.core.search.service.SearXNGProvider", None), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()), \
         mock.patch("app.core.search.service.asyncio.sleep", mock.AsyncMock()):
        resp = service.search(SearchRequest(query="flaky"))
    assert calls["n"] == 2 and resp.provider == "fake"


def test_select_target_urls_dedup_and_ssrf(tmp_path):
    service, db = _svc(tmp_path)
    r = _resp(n=4)
    r.results.organic.append(OrganicResult(position=5, title="x", url="http://127.0.0.1/admin"))
    # per_domain=0: only the SSRF-unsafe URL is dropped
    urls = service.select_target_urls([r], limit=10, per_domain=0)
    assert all(not u.startswith("http://127.0.0.1") for u in urls)
    assert len(urls) == 4
    # per_domain=1: one URL per domain (all four share a.com)
    assert len(service.select_target_urls([r], limit=10, per_domain=1)) == 1
    # limit respected
    assert len(service.select_target_urls([r], limit=2, per_domain=0)) == 2


def test_metrics_recorded_in_db(tmp_path):
    service, db = _svc(tmp_path)
    fake = _FakeProvider(n=2)
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=fake), \
         mock.patch("app.core.search.service.SearXNGProvider", None), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()):
        service.search(SearchRequest(query="metrics query"))
        service.search(SearchRequest(query="metrics query"))
    snap = service.metrics.snapshot()
    assert snap["requests"] == 2
    assert snap["cache_hits"] == 1
    assert snap["served"] == 2 and snap["failures"] == 0


def test_search_many_concurrent(tmp_path):
    service, db = _svc(tmp_path)
    fake = _FakeProvider(n=1)
    with mock.patch("app.core.search.service.DDGHTMLProvider", return_value=fake), \
         mock.patch("app.core.search.service.SearXNGProvider", None), \
         mock.patch("app.core.search.service.http_client"), \
         mock.patch("app.core.search.service.asyncio.Semaphore", mock.MagicMock()):
        reqs = [SearchRequest(query=f"batch {i}") for i in range(5)]
        results = service.search_many(reqs)
    assert all(isinstance(r, SearchResponse) for r in results)
    assert fake.calls == 5
