from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.search.schema import ProviderFailure, SearchRequest
from app.core.search.providers.ddg_html import DDGHTMLProvider, parse_results
from app.core.search.providers.searxng import SearXNGProvider

# Representative snapshot of the html.duckduckgo.com result markup
# (structure, not full page).
DDG_SAMPLE = """
<div class="result results_links results_links_deep">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.poolcorp.com%2F&utm_term=x&rut=abc">
      POOLCORP - Swimming Pool Supplies
    </a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.poolcorp.com%2F&rut=abc">Pool supplies distributor <b>worldwide</b>.</a>
</div>
<div class="result">
  <a class="result__a" href="https://www.another.com/page?utm_source=ddg">Another Direct Link</a>
  <a class="result__snippet">Snippet two</a>
</div>
"""

DDG_ANOMALY = "<html><body><div class='anomaly'>Unfortunately, bots use DuckDuckGo too</div></body></html>"


def test_ddg_parse_results_unwraps_and_normalizes():
    out = parse_results(DDG_SAMPLE)
    assert len(out) == 2
    assert out[0].url == "https://www.poolcorp.com/"
    assert out[0].position == 1
    assert out[0].snippet == "Pool supplies distributor worldwide."
    assert out[1].url == "https://www.another.com/page"      # utm stripped
    assert out[1].title == "Another Direct Link"


def test_ddg_parse_malformed_html_returns_empty_not_crash():
    assert parse_results("<html><body>junk<</body>") == []
    assert parse_results("") == []


def test_ddg_parse_dedupes_within_page():
    sample = DDG_SAMPLE + DDG_SAMPLE
    out = parse_results(sample)
    assert len(out) == 2


def test_ddg_blocked_page_raises():
    import asyncio
    from app.core.search.providers.base import http_client

    class _Resp:
        status_code = 200
        text = DDG_ANOMALY
        def raise_for_status(self): pass

    provider = DDGHTMLProvider()

    async def run():
        client = http_client()
        async with client:
            async def fake_get(*a, **k):
                return _Resp()
            client.get = fake_get
            return await provider.search(SearchRequest(query="test"), client)

    try:
        asyncio.run(run())
    except ProviderFailure as e:
        assert e.kind == "blocked"
    else:
        raise AssertionError("anomaly page did not raise blocked")


def test_ddg_kl_mapping():
    p = DDGHTMLProvider()
    assert p._params(SearchRequest(query="q", country="us", language="en"))["kl"] == "us-en"
    assert p._params(SearchRequest(query="q", country="eg", language="ar"))["kl"] == "eg-ar"
    assert p._params(SearchRequest(query="q"))["kl"] == "wt-wt"
    # pagination offset: page 3 x 10 results -> s=20
    params = p._params(SearchRequest(query="q", page=3, num_results=10))
    assert params["s"] == "20" and params["dc"] == "21"


def test_searxng_parse_and_slice():
    import asyncio
    from app.core.search.providers.base import http_client

    payload = {
        "results": [
            {"title": "A", "url": "https://a.com/?utm_source=searxng", "content": "snip A"},
            {"title": "B", "url": "javascript:void(0)", "content": None},
            {"title": "A", "url": "https://a.com/", "content": "dup"},
        ],
        "suggestions": [{"q": "related query"}],
    }

    class _Resp:
        status_code = 200
        text = ""
        def raise_for_status(self): pass
        def json(self): return payload

    provider = SearXNGProvider(base_url="http://127.0.0.1:8888")

    async def run():
        client = http_client()
        async with client:
            async def fake_get(*a, **k):
                return _Resp()
            client.get = fake_get
            return await provider.search(SearchRequest(query="q"), client)

    blocks = asyncio.run(run())
    assert len(blocks.organic) == 1
    assert blocks.organic[0].url == "https://a.com/"
    assert blocks.related_searches == ["related query"]


def test_provider_failure_mapping():
    import httpx
    from app.core.search.providers.base import raise_provider_failure

    req = httpx.Request("GET", "https://x.com")
    resp403 = httpx.Response(403, request=req)
    assert raise_provider_failure("p", httpx.HTTPStatusError("x", request=req, response=resp403)).kind == "blocked"
    resp500 = httpx.Response(500, request=req)
    assert raise_provider_failure("p", httpx.HTTPStatusError("x", request=req, response=resp500)).kind == "unavailable"
    assert raise_provider_failure("p", httpx.ConnectTimeout("t", request=req)).kind == "timeout"
    assert raise_provider_failure("p", httpx.ConnectError("n")).kind == "network"
