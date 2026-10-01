"""
LOGY Search — stable internal SERP schema.

The rest of LOGY talks ONLY to this schema and to SearchService
(app/core/search/service.py). Providers (DDG HTML, SearXNG, ...) are
normalized INTO this shape, so swapping a provider never touches callers.

Never fake unavailable data: a provider that cannot supply a block
(news/images/places/...) returns [] there, and its limitation is declared
in ProviderCapabilities and surfaced in the response `warnings`.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    """One search request — mirrors the internal SERP contract.

    `type` selects the upstream category when the provider supports it:
    "search" (organic), "news", "images". Providers that don't support a
    type are skipped by the service (capability match), never faked.
    """
    query: str
    page: int = Field(default=1, ge=1, le=100)
    location: Optional[str] = None       # free-text, provider-agnostic hint
    country: Optional[str] = None        # ISO-ish code, e.g. "us", "eg"
    language: Optional[str] = None       # e.g. "en", "ar"
    num_results: int = Field(default=10, ge=1, le=100)
    type: str = Field(default="search", pattern="^(search|news|images)$")

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be empty")
        if len(v) > 400:
            raise ValueError("query too long (max 400 chars)")
        return v

    def cache_key(self) -> str:
        """Deterministic cache key over every parameter that changes the
        result — repeated ICP queries must hit cache, never the engine."""
        import hashlib
        import json as _json
        payload = _json.dumps(
            [self.query, self.page, self.location, self.country,
             self.language, self.num_results, self.type],
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class OrganicResult(BaseModel):
    position: int                      # 1-based, ranking preserved
    title: str
    url: str
    displayed_url: Optional[str] = None
    snippet: Optional[str] = None


class ResultBlocks(BaseModel):
    """Every block always present (stable shape); unavailable = []/None."""
    organic: list[OrganicResult] = Field(default_factory=list)
    news: list[OrganicResult] = Field(default_factory=list)
    images: list[OrganicResult] = Field(default_factory=list)
    places: list[OrganicResult] = Field(default_factory=list)
    related_questions: list[str] = Field(default_factory=list)
    related_searches: list[str] = Field(default_factory=list)
    knowledge_graph: Optional[dict] = None


class ResponseMeta(BaseModel):
    total_returned: int
    latency_ms: int
    cached: bool
    ts: float = Field(default_factory=time.time)


class SearchResponse(BaseModel):
    request_id: str
    query: str
    page: int
    provider: str                       # provider that actually served it
    results: ResultBlocks
    meta: ResponseMeta
    warnings: list[str] = Field(default_factory=list)  # e.g. provider limitations


class ProviderFailure(Exception):
    """Structured provider failure — the service turns these into
    fallback/error decisions, callers never see raw provider internals."""
    kind: str

    def __init__(self, provider: str, kind: str, detail: str = ""):
        self.provider = provider
        self.kind = kind        # timeout | network | blocked | parse | unavailable
        self.detail = detail
        super().__init__(f"[{provider}] {kind}: {detail}")


class ProviderCapabilities(BaseModel):
    organic: bool = True
    news: bool = False
    images: bool = False
    places: bool = False
    related: bool = False
    knowledge_graph: bool = False


def new_request_id() -> str:
    return "req_" + uuid.uuid4().hex[:12]
