"""Search providers package - the engine seam. LOGY imports only this
(plus schema/service); engines themselves are never imported elsewhere."""
from app.core.search.providers.base import (
    ProviderCapabilities, ProviderFailure, SearchProvider, http_client,
    raise_provider_failure,
)
from app.core.search.providers.ddg_html import DDGHTMLProvider
from app.core.search.providers.searxng import SearXNGProvider

__all__ = [
    "SearchProvider", "ProviderCapabilities", "ProviderFailure",
    "DDGHTMLProvider", "SearXNGProvider",
    "http_client", "raise_provider_failure",
]
