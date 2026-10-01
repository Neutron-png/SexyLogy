from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.search.schema import SearchRequest, SearchResponse, ResultBlocks, OrganicResult
from app.core.search.urls import normalize_url, dedupe_urls, domain_of, is_safe_outbound_url, unwrap_redirect


def test_normalize_url_strips_tracking_and_canonicalizes():
    assert normalize_url("HTTPS://WWW.Example.com/A/?utm_source=x&fbclid=1&id=2") == "https://www.example.com/A/?id=2"
    assert normalize_url("https://example.com:443") == "https://example.com/"
    assert normalize_url("http://example.com:80/x") == "http://example.com/x"
    assert normalize_url("//example.com/a") == "https://example.com/a"
    assert normalize_url("javascript:void(0)") == ""
    assert normalize_url("") == ""


def test_unwrap_search_redirects():
    enc = "https%3A%2F%2Fexample.org%2Fpage"
    assert unwrap_redirect(f"https://duckduckgo.com/l/?uddg={enc}&rut=abc") == "https://example.org/page"
    assert unwrap_redirect("https://example.org/direct") == "https://example.org/direct"


def test_unwrap_bing_base64_redirect():
    """Bing ck/a links carry `u=a1<base64url>` - the real destination."""
    import base64
    dest = "https://www.yelp.com/search?find_desc=Pool+Builders"
    b64 = "a1" + base64.urlsafe_b64encode(dest.encode()).decode().rstrip("=")
    from app.core.search.urls import normalize_url
    assert normalize_url(f"https://www.bing.com/ck/a?!&p=x&u={b64}&ntb=1").startswith("https://www.yelp.com/")


def test_dedupe_preserves_first_position():
    urls = [
        "https://a.com/x?utm_source=t",
        "https://a.com/x",
        "https://b.com/y",
        "https://a.com/x",
        "junk",
    ]
    assert dedupe_urls(urls) == ["https://a.com/x", "https://b.com/y"]
    # www vs apex are distinct hosts for URL-dedup; domain_of unifies them
    urls2 = ["https://a.com/x", "https://www.a.com/x"]
    assert len(dedupe_urls(urls2)) == 2
    assert domain_of(urls2[0]) == domain_of(urls2[1])


def test_domain_of():
    assert domain_of("https://www.sub.Example.com:443/a?b=1") == "sub.example.com"
    assert domain_of("junk") == ""


def test_ssrf_guard():
    assert is_safe_outbound_url("https://example.com") is True
    assert is_safe_outbound_url("http://127.0.0.1/x") is False
    assert is_safe_outbound_url("http://localhost/x") is False
    assert is_safe_outbound_url("file:///etc/passwd") is False
    assert is_safe_outbound_url("http://169.254.169.254/meta") is False
    assert is_safe_outbound_url("http://192.168.1.1/") is False
    assert is_safe_outbound_url("") is False


def test_request_validation():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SearchRequest(query="   ")
    with pytest.raises(ValidationError):
        SearchRequest(query="x" * 401)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", page=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", num_results=0)
    with pytest.raises(ValidationError):
        SearchRequest(query="x", type="videos")
    r = SearchRequest(query="  عيادات أسنان  ")
    assert r.query == "عيادات أسنان"


def test_cache_key_deterministic_and_order_insensitive():
    a = SearchRequest(query="q", location="NY", country="us", language="en")
    b = SearchRequest(query="q", language="en", location="NY", country="us")
    assert a.cache_key() == b.cache_key()
    c = SearchRequest(query="q", page=2)
    assert c.cache_key() != a.cache_key()


def test_response_stable_shape_blocks_present_even_when_empty():
    resp = SearchResponse(
        request_id="req_x", query="q", page=1, provider="ddg_html",
        results=ResultBlocks(organic=[OrganicResult(position=1, title="T", url="https://a.com")]),
        meta={"total_returned": 1, "latency_ms": 5, "cached": False},
    )
    assert resp.results.news == [] and resp.results.images == []
    assert resp.results.places == [] and resp.results.knowledge_graph is None
    assert resp.warnings == []
