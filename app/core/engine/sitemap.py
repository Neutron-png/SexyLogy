"""
Sitemap-first URL discovery: gather crawl targets from the site's own
sitemap.xml instead of hammering protected search/listing pages.

Why this matters for anonymity: search pages are the MOST protected pages
on any directory site (they're what the WAF is tuned for), while sitemap
files are served statically from CDN edges with basically no protection
and no rate limiting. Reading them costs ~1-3 requests and can surface
hundreds of per-business detail URLs - the same URLs the search crawl
would have fought for, without touching the wall once.

stdlib only: the sitemap XML is parsed with regex on <loc> entries (good
enough for the well-formed XML sitemaps actually deployed; malformed
edge cases simply yield no URLs and the job falls back to normal crawling).
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[(?P<cdata>.*?)\]\]>|(?P<plain>[^<]*?))\s*</loc>", re.S | re.I)

_SITEMAP_CANDIDATES = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml", "/wp-sitemap.xml")


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """Split a sitemap body into (urls, child_sitemaps). Handles both
    <urlset> documents and <sitemapindex> documents, CDATA-wrapped locs,
    and whitespace noise."""
    urls, children = [], []
    for m in _LOC_RE.finditer(xml):
        loc = (m.group("cdata") or m.group("plain") or "").strip()
        if not loc:
            continue
        (children if loc.rstrip("/").endswith((".xml", ".xml.gz")) or "/sitemap" in loc.lower() else urls).append(loc)
    return urls, children


def discover_sitemap_urls(
    start_urls: list[str],
    fetcher,                       # callable(url) -> str|None (raw body); injected, not imported
    include_patterns: Optional[list[str]] = None,
    exclude_patterns: Optional[list[str]] = None,
    max_urls: int = 500,
    max_children: int = 5,
) -> list[str]:
    """Expand start URLs through their sitemaps. `fetcher` is injected by
    the caller (the job manager binds it to engine.fetch_one + get_html) so
    this module stays network-agnostic and testable.

    Returns the discovered URLs (already filtered by include/exclude and
    capped), WITHOUT the original start URLs - the caller merges both."""
    import fnmatch

    discovered: list[str] = []
    seen: set[str] = set()
    children_done: set[str] = set()

    def _matches(url: str) -> bool:
        if exclude_patterns and any(fnmatch.fnmatch(url, p) for p in exclude_patterns):
            return False
        if include_patterns:
            return any(fnmatch.fnmatch(url, p) for p in include_patterns)
        return True

    for start in start_urls:
        if len(discovered) >= max_urls:
            break
        base = f"{urlparse(start).scheme}://{urlparse(start).netloc}"

        roots = [urljoin(base + "/", path) for path in _SITEMAP_CANDIDATES]
        index_xml = None
        for candidate in roots:
            body = fetcher(candidate)
            if body and "<loc" in body.lower():
                index_xml = body
                break
        if index_xml is None:
            continue

        urls, children = parse_sitemap(index_xml)
        # sitemap index: recurse into a bounded number of child sitemaps
        for child in children[:max_children]:
            if child in children_done:
                continue
            children_done.add(child)
            if len(discovered) >= max_urls:
                break
            child_body = fetcher(child)
            if child_body:
                c_urls, _ = parse_sitemap(child_body)
                urls.extend(c_urls)

        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            if not _matches(url):
                continue
            # keep the target domain only - sitemaps list canonical URLs,
            # but CDNs sometimes leak alternate hosts
            if urlparse(url).netloc.lower() != urlparse(start).netloc.lower():
                continue
            discovered.append(url)
            if len(discovered) >= max_urls:
                break

    return discovered
