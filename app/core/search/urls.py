"""URL normalization + deduplication for search results.

Search engines return noisy URLs (tracking params, redirects, fragments,
mixed case hosts, mirror duplicates). Every result passes through
normalize_url() before it reaches callers, and dedupe_urls() keeps the
first occurrence's ranking position.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlparse, urlunparse

# Tracking params stripped on normalization - they bloat cache keys,
# break dedup, and leak nothing the crawler needs.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "msclkid", "dclid", "yclid", "igshid", "mc_cid",
    "mc_eid", "_ga", "ref", "ref_src",
}

# query params whose value is itself an encoded destination URL
_ENCODED_URL_PARAMS = {"uddg", "url", "u", "target"}


def unwrap_redirect(url: str) -> str:
    """Search providers wrap destinations in their own redirectors
    (duckduckgo.com/l/?uddg=<encoded>, google url?q=...). Unwrap one
    level; unknown wrappers pass through
    untouched."""
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.netloc.lower() not in ("duckduckgo.com", "www.google.com",
                                     "www.bing.com", "l.facebook.com",
                                     "lm.facebook.com", "out.reddit.com"):
        return url
    qs = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for p in _ENCODED_URL_PARAMS:
        if p in qs and qs[p].startswith(("http://", "https://", "//")):
            return unquote(qs[p])
    return url


def normalize_url(url: str) -> str:
    """Canonical form: real destination, lowercase scheme+host, no
    fragment, no tracking params, no default ports, sorted query."""
    url = unwrap_redirect((url or "").strip())
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    scheme = parsed.scheme.lower()
    host = parsed.netloc.lower()
    if host.endswith(":80") and scheme == "http":
        host = host[:-3]
    if host.endswith(":443") and scheme == "https":
        host = host[:-4]
    query_list = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                  if k.lower() not in _TRACKING_PARAMS]
    query_list.sort()
    return urlunparse((scheme, host, parsed.path or "/", "",
                       urlencode(query_list), ""))


def domain_of(url: str) -> str:
    host = urlparse(normalize_url(url)).netloc
    return host[4:] if host.startswith("www.") else host


def dedupe_urls(urls: list[str]) -> list[str]:
    """First occurrence wins (ranking position preserved); normalized
    empties (junk URLs a provider returned) are dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        n = normalize_url(u)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def is_safe_outbound_url(url: str) -> bool:
    """SSRF guard for anything that will later be FETCHED (not just
    parsed): only http(s) to a public host - never file/ftp, never
    localhost/loopback/link-local metadata endpoints."""
    n = normalize_url(url)
    if not n:
        return False
    p = urlparse(n)
    if p.scheme not in ("http", "https"):
        return False
    host = p.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1", ""):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        import ipaddress
        ip = ipaddress.ip_address(host)
        return not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved)
    except ValueError:
        return True  # a plain hostname, not an IP literal


def join_url(base: str, href: str) -> str:
    return urljoin(base, href)


__all__ = [
    "normalize_url", "unwrap_redirect", "domain_of", "dedupe_urls",
    "is_safe_outbound_url", "join_url", "quote",
]
