"""
DDG HTML provider - LOGY's zero-infra default search source.

Uses DuckDuckGo's lightweight HTML endpoint (html.duckduckgo.com) - the
no-JS, no-key, free surface DDG documents for basic access. Pages are
parsed with stdlib HTMLParser (no bs4, no browser, no JS).

Limitations (declared in capabilities, never faked):
- organic results only; no news/images/places/knowledge-graph blocks.
- DDG pagination is offset-based (`s=`), page size is DDG's own, so
  `page`/`num_results` are honored by offset + slice.

If DDG blocks/limits automated access (anomaly pages, CAPTCHA), this
provider raises ProviderFailure("blocked") - the service falls back to
another provider or fails gracefully. Nothing here tries to bypass it.
"""
from __future__ import annotations

import html as _html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, quote

from app.core.search.providers.base import (
    SearchProvider, ProviderCapabilities, ProviderFailure, raise_provider_failure,
)
from app.core.search.schema import (
    OrganicResult, ProviderCapabilities as _Caps, ResultBlocks, SearchRequest,
)
from app.core.search.urls import normalize_url, unwrap_redirect

HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


def _kl(country: str | None, language: str | None) -> str:
    """DDG's region-lang kl param, e.g. us-en / eg-ar; wt-wt = no region."""
    if country and language:
        return f"{country.lower()}-{language.lower()}"
    if country:
        return f"{country.lower()}-en"
    if language:
        return f"wt-wt-{language.lower()}"
    return "wt-wt"


class _DDGParser(HTMLParser):
    """Extracts (title, href, snippet) triples from the html endpoint's
    markup: a.result__a (link) followed by a.result__snippet."""

    def __init__(self):
        super().__init__()
        self.results: list[tuple[str, str, str]] = []
        self._in_link = False
        self._in_snippet = False
        self._cur_href = ""
        self._cur_title: list[str] = []
        self._cur_snippet: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._in_link = True
            self._cur_href = a.get("href", "")
            self._cur_title = []
        elif tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
            self._cur_snippet = []

    def handle_endtag(self, tag):
        if tag == "a" and self._in_link:
            self._in_link = False
        elif tag == "a" and self._in_snippet:
            self._in_snippet = False

    def handle_data(self, data):
        if self._in_link:
            self._cur_title.append(data)
        elif self._in_snippet:
            self._cur_snippet.append(data)

    def close(self):
        super().close()
        titles = self._collect()
        base = []
        for href, title in titles:
            # snippets live right after their link in document order;
            # match by collecting the next parsed snippet per link
            base.append((href, title, ""))
        # second pass: attach snippets by order of appearance
        snippets = self._snippets
        for i, (href, title, _) in enumerate(base):
            base[i] = (href, title, snippets[i] if i < len(snippets) else "")
        self.results = base

    def _collect(self) -> list[tuple[str, str]]:
        out = []
        for href, title in zip(self._links, self._titles):
            out.append((href, title))
        return out


class _DDGHTMLParser(HTMLParser):
    """Order-preserving single-pass parser: every a.result__a opens a
    record; every a.result__snippet closes the previous one."""

    _RESULT = re.compile(r"result__a")
    _SNIPPET = re.compile(r"result__snippet")

    def __init__(self):
        super().__init__()
        self.records: list[dict] = []
        self._cur: dict | None = None
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "a" and self._RESULT.search(cls):
            if self._cur and self._cur.get("title"):
                self.records.append(self._cur)
            self._cur = {"href": a.get("href", ""), "title": [], "snippet": []}
        elif tag == "a" and self._SNIPPET.search(cls):
            self._in_snippet = True
            if self._cur is not None:
                self._cur["snippet"] = []

    def handle_endtag(self, tag):
        if tag == "a":
            self._in_snippet = False

    def handle_data(self, data):
        if self._cur is not None:
            if self._in_snippet:
                self._cur["snippet"].append(data)
            else:
                self._cur["title"].append(data)


def _decode_ddg_href(href: str) -> str:
    """DDG wraps destinations: //duckduckgo.com/l/?uddg=<enc>&rut=..."""
    href = unwrap_redirect(href) if href.startswith("//") else href
    if "uddg=" in href:
        qs = dict(parse_qsl(href.split("?", 1)[1], keep_blank_values=True))
        if "uddg" in qs:
            return qs["uddg"]
    return href


def parse_results(html_text: str) -> list[OrganicResult]:
    parser = _DDGHTMLParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        pass  # malformed markup -> whatever was parsed so far
    if parser._cur and parser._cur.get("title"):
        parser.records.append(parser._cur)

    out: list[OrganicResult] = []
    seen: set[str] = set()
    for rec in parser.records:
        title = _html.unescape("".join(rec["title"])).strip()
        raw_href = _html.unescape(rec["href"] or "")
        url = normalize_url(_decode_ddg_href(raw_href))
        snippet = _html.unescape("".join(rec["snippet"])).strip()
        if not title or not url or url in seen:
            continue
        seen.add(url)
        out.append(OrganicResult(position=len(out) + 1, title=title, url=url,
                                 displayed_url=None, snippet=snippet or None))
    return out


class DDGHTMLProvider(SearchProvider):
    name = "ddg_html"
    capabilities = ProviderCapabilities(organic=True)   # organic only - declared, never faked

    def __init__(self, proxy: str | None = None):
        self._proxy = proxy

    def _params(self, request: SearchRequest) -> dict:
        offset = (request.page - 1) * request.num_results
        params = {"q": request.query, "kl": _kl(request.country, request.language)}
        if offset:
            params["s"] = str(offset)
            params["dc"] = str(offset + 1)
            params["o"] = "json"
        if request.type == "news":
            params["iar"] = "news"
            params["df"] = "w"
        return params

    async def search(self, request: SearchRequest, client: Any) -> ResultBlocks:
        try:
            resp = await client.get(HTML_ENDPOINT, params=self._params(request))
            resp.raise_for_status()
        except Exception as e:
            raise raise_provider_failure(self.name, e) from e

        text = resp.text
        # DDG anomaly/CAPTCHA pages contain no result markup at all -
        # treated as 'blocked' so the service can fall back, never retried
        # against the same wall.
        if "anomaly" in text.lower() or ("challenge" in text.lower() and "result__a" not in text):
            if "result__a" not in text:
                raise ProviderFailure(self.name, "blocked", "DDG anomaly/CAPTCHA page")

        organic = parse_results(text)
        if request.num_results < len(organic):
            organic = organic[:request.num_results]
        return ResultBlocks(organic=organic)


__all__ = ["DDGHTMLProvider", "parse_results", "HTML_ENDPOINT", "quote", "re"]
