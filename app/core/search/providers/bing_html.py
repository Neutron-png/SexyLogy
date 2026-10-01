"""
Bing HTML provider - LOGY's free fallback search source.

Parses Bing's server-rendered /search results page with stdlib
HTMLParser (no key, no browser, no JS, no CAPTCHA bypassing). Its role
in the chain: when the primary (DDG HTML) serves an anomaly/CAPTCHA
page (kind='blocked'), the service falls back here - same zero-cost
category (a public SERP surface), no paid API.

Limitations (declared, never faked): organic results only. If Bing
itself serves a challenge page, this provider raises
ProviderFailure('blocked') like any other - and the service fails
gracefully.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, quote, unquote

from app.core.search.providers.base import (
    SearchProvider, ProviderFailure, raise_provider_failure,
)
from app.core.search.schema import (
    OrganicResult, ProviderCapabilities, ResultBlocks, SearchRequest,
)
from app.core.search.urls import normalize_url

HTML_ENDPOINT = "https://www.bing.com/search"


class _BingHTMLParser(HTMLParser):
    """li.b_algo -> h2 > a(title, href) + .b_caption p (snippet)."""

    _ALGO = re.compile(r"\bb_algo\b")

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._cur: dict | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "li" and self._ALGO.search(cls):
            if self._cur and self._cur["title"]:
                self.records.append(self._cur)
            self._cur = {"href": "", "title": [], "snippet": []}
        elif tag == "a" and self._cur is not None and not self._cur["href"] and "href" in a:
            self._cur["href"] = a.get("href", "")
            self._in_title = True
        elif tag == "p" and self._cur is not None:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_title = False
        elif tag == "p":
            self._in_snippet = False
        elif tag == "li" and self._cur is not None:
            if self._cur["title"]:
                self.records.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._cur is None:
            return
        if self._in_title:
            self._cur["title"].append(data)
        elif self._in_snippet:
            self._cur["snippet"].append(data)


def parse_results(html_text: str) -> list[OrganicResult]:
    parser = _BingHTMLParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        pass
    if parser._cur and parser._cur["title"]:
        parser.records.append(parser._cur)

    out: list[OrganicResult] = []
    seen: set[str] = set()
    for rec in parser.records:
        title = _html.unescape("".join(rec["title"])).strip()
        url = normalize_url(_html.unescape(rec["href"] or ""))
        snippet = _html.unescape("".join(rec["snippet"])).strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)
        out.append(OrganicResult(position=len(out) + 1, title=title, url=url,
                                 displayed_url=None, snippet=snippet or None))
    return out


class BingHTMLProvider(SearchProvider):
    name = "bing_html"
    capabilities = ProviderCapabilities(organic=True)   # organic only - declared

    def __init__(self, proxy: str | None = None):
        del proxy  # the search layer is deliberately never proxied

    def _params(self, request: SearchRequest) -> dict:
        params = {"q": request.query}
        if request.country:
            params["cc"] = request.country.lower()
        if request.language:
            params["setlang"] = request.language.lower()
        offset = (request.page - 1) * request.num_results
        if offset:
            params["first"] = str(offset + 1)
        if request.type == "news":
            params["qft"] = "news"
        return params

    async def search(self, request: SearchRequest, client: Any) -> ResultBlocks:
        try:
            resp = await client.get(HTML_ENDPOINT, params=self._params(request))
            resp.raise_for_status()
        except Exception as e:
            raise raise_provider_failure(self.name, e) from e

        text = resp.text
        low = text.lower()
        if "result__a" not in low and ("b_algo" not in low):
            if ("captcha" in low or "challenge" in low or "verify" in low
                    or "unusual" in low or len(text) < 4000):
                raise ProviderFailure(self.name, "blocked", "Bing challenge/verification page")

        organic = parse_results(text)
        if request.num_results < len(organic):
            organic = organic[:request.num_results]
        return ResultBlocks(organic=organic)


__all__ = ["BingHTMLProvider", "parse_results", "HTML_ENDPOINT", "unquote"]
