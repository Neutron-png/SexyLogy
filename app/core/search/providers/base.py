"""
Search provider abstraction - the ONLY seam between LOGY and upstream
search engines. LOGY never talks to an engine directly (architectural
principle: no engine hard-coding outside providers/)."""
from __future__ import annotations

import abc
from typing import Optional

import httpx

from app.core.search.schema import (
    ProviderCapabilities, ProviderFailure, ResultBlocks, SearchRequest,
)


class SearchProvider(abc.ABC):
    """One upstream engine. Async (httpx) so the service can run many
    searches concurrently without browsers; a provider launches browser
    processes only if IT genuinely needs to (none of the built-ins do).

    Contract: return normalized ResultBlocks or raise ProviderFailure.
    NEVER return fabricated data - if a block can't be parsed, the block
    stays empty and the capabilities say so."""

    name: str = "provider"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def can_serve(self, request: SearchRequest) -> bool:
        requested = getattr(self.capabilities, request.type if request.type != "search" else "organic")
        return bool(requested)

    @abc.abstractmethod
    async def search(self, request: SearchRequest, client: httpx.AsyncClient) -> ResultBlocks:
        ...


def http_client(timeout_s: float = 15.0, proxy: Optional[str] = None) -> httpx.AsyncClient:
    """Connection-pooled async client with a bounded timeout. Follows
    redirects (providers redirect); verifies TLS by default; optional
    egress proxy for environments that require one."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s, connect=10.0),
        follow_redirects=True,
        proxy=proxy,
        headers={"User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")},
    )


def raise_provider_failure(provider: str, exc: Exception) -> ProviderFailure:
    """Map httpx/network exceptions to the structured failure kinds the
    service uses for fallback/breaker decisions."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderFailure(provider, "timeout", str(exc))
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        kind = "blocked" if code in (403, 429, 503) else "unavailable"
        return ProviderFailure(provider, kind, f"HTTP {code}")
    if isinstance(exc, httpx.HTTPError):
        return ProviderFailure(provider, "network", str(exc))
    return ProviderFailure(provider, "unavailable", str(exc))
