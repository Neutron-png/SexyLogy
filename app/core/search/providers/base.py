"""Search provider abstraction - the ONLY seam between LOGY and upstream
search engines. LOGY never talks to an engine directly (architectural
principle: no engine hard-coding outside providers/).

HTTP layer: curl_cffi with Chrome TLS impersonation - direct connection,
NEVER proxied (the user's decision: the search layer uses curl, not the
crawler's proxy/Tor settings; those stay crawler-only). No key, no
browser, no CAPTCHA bypassing - if an engine blocks the request we
fail with kind='blocked' and the service falls back or errors.
"""
from __future__ import annotations

import abc
from typing import Any, Optional

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import HTTPError, RequestException, Timeout

from app.core.search.schema import (
    ProviderCapabilities, ProviderFailure, ResultBlocks, SearchRequest,
)

DEFAULT_TIMEOUT_S = 15.0


class CurlSession:
    """Thin async wrapper so providers keep a simple client surface
    (get/raise_for_status/text/json) with impersonated Chrome TLS -
    and a hard guarantee: no proxy is ever attached here."""

    def __init__(self, timeout_s: float = DEFAULT_TIMEOUT_S):
        self._timeout = timeout_s
        self._session: Optional[AsyncSession] = None

    async def __aenter__(self):
        self._session = AsyncSession(impersonate="chrome")
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._session.__aexit__(*exc)

    async def get(self, url: str, **kwargs) -> Any:
        kwargs.setdefault("timeout", self._timeout)
        return await self._session.get(url, **kwargs)


def http_client(timeout_s: float = DEFAULT_TIMEOUT_S, proxy: Optional[str] = None) -> CurlSession:
    """Kept name for callers; `proxy` is accepted-but-ignored BY DESIGN:
    the search layer is explicitly direct-curl, never proxied (user
    decision). Providers needing a proxy in the future must define their
    own contract, visibly."""
    del proxy  # deliberate: never proxied
    return CurlSession(timeout_s=timeout_s)


def raise_provider_failure(provider: str, exc: Exception) -> ProviderFailure:
    """Map curl/network exceptions to the structured failure kinds the
    service uses for fallback/breaker decisions."""
    if isinstance(exc, Timeout):
        return ProviderFailure(provider, "timeout", str(exc))
    if isinstance(exc, HTTPError):
        code = getattr(getattr(exc, "response", None), "status_code", None)
        kind = "blocked" if code in (403, 429, 503) else "unavailable"
        return ProviderFailure(provider, kind, f"HTTP {code}")
    if isinstance(exc, RequestException):
        return ProviderFailure(provider, "network", str(exc))
    if isinstance(exc, Exception):
        # curl_cffi also raises bare CurlError subclasses for transport
        # problems (connect/reset) - treat as network
        return ProviderFailure(provider, "network", str(exc))
    return ProviderFailure(provider, "unavailable", str(exc))


class SearchProvider(abc.ABC):
    """One upstream engine. Async (curl_cffi impersonated, direct) so
    the service can run many searches concurrently without browsers.

    Contract: return normalized ResultBlocks or raise ProviderFailure.
    NEVER return fabricated data - if a block can't be parsed, the block
    stays empty and the capabilities say so."""

    name: str = "provider"
    capabilities: ProviderCapabilities = ProviderCapabilities()

    def can_serve(self, request: SearchRequest) -> bool:
        requested = getattr(self.capabilities, request.type if request.type != "search" else "organic")
        return bool(requested)

    @abc.abstractmethod
    async def search(self, request: SearchRequest, client: Any) -> ResultBlocks:
        ...
