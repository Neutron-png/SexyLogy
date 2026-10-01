"""
SearXNG provider - optional self-hosted aggregator (open source, zero
API cost). Enabled when the user runs SearXNG anywhere (own machine,
homelab, server) and points LOGY at its base URL with `format=json`
enabled in its settings.

Capabilities depend on the instance's configured engines - the static
declaration below covers the categories LOGY requests; blocks the
instance doesn't return come back as empty arrays, never faked.
"""
from __future__ import annotations

from urllib.parse import urlencode

import httpx

from app.core.search.providers.base import SearchProvider, raise_provider_failure
from app.core.search.schema import (
    OrganicResult, ProviderCapabilities, ResultBlocks, SearchRequest,
)
from app.core.search.urls import normalize_url

_TYPE_CATEGORY = {"search": None, "news": "news", "images": "images"}


class SearXNGProvider(SearchProvider):
    name = "searxng"
    capabilities = ProviderCapabilities(organic=True, news=True, images=True, related=True)

    def __init__(self, base_url: str, proxy: str | None = None):
        if not base_url:
            raise ValueError("SearXNGProvider needs base_url")
        self.base_url = base_url.rstrip("/")
        self._proxy = proxy

    def _params(self, request: SearchRequest) -> dict:
        params = {"q": request.query, "format": "json",
                  "pageno": request.page, "safesearch": 1}
        cat = _TYPE_CATEGORY.get(request.type)
        if cat:
            params["categories"] = cat
        if request.language:
            params["language"] = request.language
        return params

    async def search(self, request: SearchRequest, client: httpx.AsyncClient) -> ResultBlocks:
        try:
            resp = await client.get(f"{self.base_url}/search", params=self._params(request))
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise raise_provider_failure(self.name, e) from e

        def _mk(rows: list[dict]) -> list[OrganicResult]:
            out, seen = [], set()
            for r in rows or []:
                url = normalize_url(r.get("url") or "")
                title = (r.get("title") or "").strip()
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                out.append(OrganicResult(position=len(out) + 1, title=title, url=url,
                                         displayed_url=r.get("pretty_url"),
                                         snippet=(r.get("content") or "").strip() or None))
            return out

        organic = _mk(data.get("results") or [])
        if request.num_results < len(organic):
            organic = organic[:request.num_results]
        return ResultBlocks(
            organic=organic,
            related_searches=[r.get("q", "").strip() for r in (data.get("suggestions") or [])
                              if isinstance(r, dict) and r.get("q")],
        )


__all__ = ["SearXNGProvider", "urlencode"]
