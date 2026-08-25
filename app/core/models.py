"""
Core data models for LOGY.

These are plain dataclasses used across the UI, job manager, storage and
export layers. Keeping them dependency-free (no Qt, no Scrapling imports)
means they can be unit-tested in isolation and reused by any layer.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional
import json
import time


class ExtractionType(str, Enum):
    TEXT = "text"
    ATTRIBUTE = "attribute"
    HTML = "html"
    URL = "url"
    MULTIPLE = "multiple"


class FetcherMode(str, Enum):
    FAST_HTTP = "fast_http"          # scrapling.fetchers.Fetcher / FetcherSession
    DYNAMIC_BROWSER = "dynamic"      # scrapling.fetchers.DynamicFetcher / DynamicSession
    STEALTH_BROWSER = "stealth"      # scrapling.fetchers.StealthyFetcher / StealthySession


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class LogLevel(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


@dataclass
class ExtractionField:
    """One field in the visual Field Builder (Step 2)."""
    name: str
    selector: str                      # CSS or XPath
    selector_type: str = "css"         # "css" | "xpath"
    extraction_type: ExtractionType = ExtractionType.TEXT
    attribute: Optional[str] = None    # used when extraction_type == ATTRIBUTE
    multiple: bool = False
    parent: Optional[str] = None       # optional parent field name, for nesting

    def to_dict(self) -> dict:
        d = asdict(self)
        d["extraction_type"] = self.extraction_type.value
        return d

    @staticmethod
    def from_dict(d: dict) -> "ExtractionField":
        d = dict(d)
        d["extraction_type"] = ExtractionType(d.get("extraction_type", "text"))
        return ExtractionField(**d)


@dataclass
class TargetConfig:
    start_urls: list[str] = field(default_factory=list)
    same_domain_only: bool = True
    follow_links: bool = False
    max_pages: int = 50
    max_depth: int = 1
    respect_robots_txt: bool = True
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class ProxyConfig:
    mode: str = "none"                 # "none" | "single" | "list" | "rotating"
    proxies: list[str] = field(default_factory=list)  # never logged/exported raw


@dataclass
class AIExtractionConfig:
    """Config for the no-selector 'AI Auto-Extract' mode (see
    app/core/engine/ai_extractor.py). When enabled, the extraction Field
    Builder / CSS-XPath fields are ignored entirely - the LLM reads the
    page's visible text and fills in field_names directly."""
    enabled: bool = False
    field_names: list[str] = field(default_factory=list)
    provider: str = "anthropic"        # "anthropic" | "openai" - must match a saved API Keys entry name


@dataclass
class ScrapeOptions:
    fetcher_mode: FetcherMode = FetcherMode.FAST_HTTP
    headless: bool = True
    concurrency: int = 4
    delay_ms: int = 0
    timeout_s: int = 30
    retries: int = 2
    solve_cloudflare: bool = False
    network_idle: bool = False
    disable_resources: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    # When true, any extracted record that has a non-empty "website" field
    # gets that site auto-fetched and scored by app/core/engine/qualifier.py
    # right after extraction - the "weak digital marketing" signal from the
    # ICP gets flagged automatically, no CSS/selector knowledge needed.
    auto_qualify_leads: bool = False
    ai_extraction: AIExtractionConfig = field(default_factory=AIExtractionConfig)
    # Separate from ai_extraction.enabled (that flag REPLACES Custom
    # Selector extraction entirely for the fetched page itself - see
    # job_manager.ScrapeJobWorker._extract()). This one instead adds an
    # extra per-lead step: any record with a non-empty "website" field
    # gets that site fetched and AI-read for owner_name / owner_email /
    # owner_phone / owner_linkedin_if_published, merged into the SAME
    # record alongside whatever Custom Selector already pulled from
    # yellowpages/Yelp - so a normal directory-based run can also come
    # back with owner-level contact fields, not just business-level ones.
    # Uses ai_extraction.provider for which API key to resolve (still
    # needs a real key saved on the API Keys screen). See
    # job_manager.ScrapeJobWorker._lookup_owner_contact_info().
    owner_lookup_enabled: bool = False


@dataclass
class Project:
    id: Optional[int]
    name: str
    target: TargetConfig
    fields: list[ExtractionField]
    options: ScrapeOptions
    created_at: float = field(default_factory=time.time)
    last_run_at: Optional[float] = None
    last_result_count: int = 0

    def to_json(self) -> str:
        def _default(o):
            if isinstance(o, Enum):
                return o.value
            if hasattr(o, "__dict__"):
                return o.__dict__
            raise TypeError
        return json.dumps(asdict(self), default=_default)


@dataclass
class JobRecord:
    id: Optional[int]
    project_id: Optional[int]
    status: JobStatus
    started_at: float
    finished_at: Optional[float] = None
    pages_done: int = 0
    pages_total: int = 0
    records_ok: int = 0
    records_failed: int = 0
    error: Optional[str] = None
