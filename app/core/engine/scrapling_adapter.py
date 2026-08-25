"""
The ONLY module in LOGY allowed to import Scrapling directly.

Every other layer (job manager, UI, storage) talks to the scraping engine
through the functions below, never to `scrapling` itself. That is the
"UI -> Job Manager -> Scraping Engine -> Scrapling" boundary from the
architecture doc: it lets the rest of the app be unit-tested without
Scrapling installed, and it means if Scrapling's API changes, only this
file needs to change.

API surface used here is exactly what Scrapling documents
(https://github.com/D4Vinci/Scrapling):

  scrapling.fetchers.Fetcher            -> fast HTTP, no browser
  scrapling.fetchers.FetcherSession     -> HTTP session, TLS impersonation
  scrapling.fetchers.DynamicFetcher     -> real browser (Playwright/patchright)
  scrapling.fetchers.DynamicSession     -> persistent browser session
  scrapling.fetchers.StealthyFetcher    -> anti-bot browser (Cloudflare etc.)
  scrapling.fetchers.StealthySession    -> persistent stealth session

Nothing here is invented: if a capability isn't in the table above, LOGY
does not claim to support it.
"""
from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Iterator, Optional

from app.core.models import FetcherMode, ScrapeOptions, ProxyConfig

try:
    from scrapling.fetchers import (
        Fetcher,
        FetcherSession,
        DynamicFetcher,
        DynamicSession,
        StealthyFetcher,
        StealthySession,
    )
    SCRAPLING_AVAILABLE = True
    SCRAPLING_IMPORT_ERROR = None
except Exception as e:  # ImportError, or a missing browser binary raising on import
    SCRAPLING_AVAILABLE = False
    SCRAPLING_IMPORT_ERROR = str(e)


class FetchError(Exception):
    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"{url}: {reason}")


class FetchCancelled(FetchError):
    """Raised when should_stop() flips true mid-fetch - lets the job
    manager tell 'user hit Stop' apart from a real network failure, so
    it can exit the retry loop immediately instead of treating it as
    just another failed attempt to retry."""


@dataclass
class FetchResult:
    url: str
    page: Any            # a Scrapling Selector-compatible page object
    status: Optional[int]
    ok: bool


def _proxy_kwarg(proxy: ProxyConfig) -> Optional[str]:
    """Scrapling's fetchers accept a single `proxy=` string per request.
    Rotation across a list is handled by the job manager picking a
    different entry per request, not by Scrapling itself."""
    if proxy.mode == "none" or not proxy.proxies:
        return None
    if proxy.mode == "single":
        return proxy.proxies[0]
    # "list" / "rotating": caller (job manager) selects the index and
    # passes it back in via proxy.proxies[0] for this particular call.
    return proxy.proxies[0]


def require_scrapling():
    if not SCRAPLING_AVAILABLE:
        raise RuntimeError(
            "Scrapling غير مثبت أو ناقصه اعتمادية (browser binaries). "
            f"تفاصيل: {SCRAPLING_IMPORT_ERROR}. "
            "شغّل: pip install scrapling && scrapling install"
        )


# scrapling.fetchers.Fetcher.get() is a classmethod that delegates to one
# module-level singleton instance Scrapling creates at import time
# (confirmed by reading Scrapling's own source, scrapling/fetchers/requests.py:
# `__FetcherClientInstance__ = _FetcherClient()`, shared by every call).
# That singleton mutates its own `_curl_session` attribute per request and
# is documented nowhere as thread-safe - "no explicit thread-safety
# mechanism" per its own request-handling code. LOGY's watchdog used to
# spin up a BRAND NEW OS thread (a throwaway ThreadPoolExecutor) for every
# single fetch_one() call, and on a timeout it abandoned that thread
# still running in the background (Python cannot force-kill a thread) -
# so a single earlier hang anywhere in the app's lifetime could leave an
# orphaned thread that goes on to race with every future call against
# that same shared singleton, forever, for as long as the app stays
# open. That race is the most likely cause of "No active session
# available" showing up on every single lead's website check in a row
# once it started (auto-qualify calls Fetcher.get() rapidly, once per
# lead, which is exactly the access pattern that would expose it).
#
# Fix: route every fetch_one() call through ONE persistent worker thread
# instead of a fresh one per call. That guarantees Scrapling's fetchers
# are only ever touched from a single, consistent OS thread for the
# entire life of the app - no churn, no possibility of two threads
# hitting the shared singleton at once. A hang still can't be forcibly
# killed (Python has no safe way to do that), but it can no longer
# corrupt anything else: everything else just queues behind it and
# resumes once it finally returns.
_fetch_executor: Optional[concurrent.futures.ThreadPoolExecutor] = None


def _get_fetch_executor() -> concurrent.futures.ThreadPoolExecutor:
    global _fetch_executor
    if _fetch_executor is None:
        _fetch_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="LOGY-scrapling-fetch"
        )
    return _fetch_executor


def fetch_one(url: str, options: ScrapeOptions, should_stop=None) -> FetchResult:
    """
    Fetch a single URL using the fetcher mode selected in Step 3 of the
    New Scrape wizard. Raises FetchError on failure (timeout, connection
    error, HTTP error, blocked request) - the job manager turns that into
    a per-URL job error rather than crashing the run.
    """
    require_scrapling()
    proxy = _proxy_kwarg(options.proxy)

    def _do_fetch():
        if options.fetcher_mode == FetcherMode.FAST_HTTP:
            return Fetcher.get(
                url,
                timeout=options.timeout_s,
                retries=options.retries,
                proxy=proxy,
                headers=options.headers or None,
                stealthy_headers=True,
            )
        elif options.fetcher_mode == FetcherMode.DYNAMIC_BROWSER:
            return DynamicFetcher.fetch(
                url,
                headless=options.headless,
                network_idle=options.network_idle,
                disable_resources=options.disable_resources,
                timeout=options.timeout_s * 1000,
                proxy=proxy,
            )
        elif options.fetcher_mode == FetcherMode.STEALTH_BROWSER:
            return StealthyFetcher.fetch(
                url,
                headless=options.headless,
                network_idle=options.network_idle,
                solve_cloudflare=options.solve_cloudflare,
                timeout=options.timeout_s * 1000,
                proxy=proxy,
            )
        else:
            raise FetchError(url, f"وضع جلب غير معروف: {options.fetcher_mode}")

    # Hard watchdog: some targets (heavy anti-bot walls, dead proxies, a
    # firewall silently dropping packets) can make the underlying network
    # call hang far longer than the `timeout=` kwarg we pass Scrapling -
    # that kwarg only bounds Scrapling's *own* wait, not every possible
    # hang below it (TCP connect stalls, a browser process that never
    # responds, etc). Running the call in the persistent single-worker
    # executor (see _get_fetch_executor() above) and bounding it with
    # `future.result(timeout=...)` here guarantees LOGY itself never waits
    # forever on one URL, regardless of what Scrapling/the network does -
    # while still only ever using that one stable worker thread, never a
    # freshly spawned one, to avoid racing Scrapling's shared fetcher
    # singleton (see the long comment above _get_fetch_executor()).
    watchdog_seconds = options.timeout_s + 15  # a little slack over Scrapling's own timeout
    poll_interval = 0.25  # how quickly a Stop click can interrupt an in-flight fetch
    executor = _get_fetch_executor()
    try:
        future = executor.submit(_do_fetch)
        elapsed = 0.0
        while True:
            if should_stop is not None and should_stop():
                future.cancel()  # no-op if already running, but frees it if still queued
                raise FetchCancelled(url, "أوقفه المستخدم")
            try:
                page = future.result(timeout=poll_interval)
                break
            except concurrent.futures.TimeoutError:
                elapsed += poll_interval
                if elapsed >= watchdog_seconds:
                    # We stop WAITING on it, but - unlike the old per-call
                    # pool - we never tear down the executor itself, so
                    # the one persistent worker thread stays exactly one
                    # thread; the hung call just keeps running on it and
                    # the next fetch_one() call queues behind it until it
                    # finally returns (Python cannot force-kill a running
                    # thread, so there was never a way to truly abort this
                    # - the old code's "abandon it" was equally unable to,
                    # it just also leaked a whole extra OS thread doing so).
                    raise FetchError(url, f"تجاوز {watchdog_seconds}s من غير رد (hang) - جرّب موقع تاني أو زوّد الـ Timeout")
    except (FetchError, FetchCancelled):
        raise
    except Exception as e:
        raise FetchError(url, str(e)) from e

    status = getattr(page, "status", None)
    ok = status is None or (200 <= int(status) < 400)
    if not ok:
        raise FetchError(url, f"HTTP {status}")

    return FetchResult(url=url, page=page, status=status, ok=ok)


def make_session(options: ScrapeOptions):
    """
    Build a persistent session object for a multi-page crawl, so LOGY
    reuses one browser/connection instead of paying startup cost per page
    (this backs the "Follow links" / pagination options in Step 1 & 3).
    Caller is responsible for using it as a context manager.
    """
    require_scrapling()
    proxy = _proxy_kwarg(options.proxy)

    if options.fetcher_mode == FetcherMode.FAST_HTTP:
        return FetcherSession(impersonate="chrome", proxy=proxy)
    if options.fetcher_mode == FetcherMode.DYNAMIC_BROWSER:
        return DynamicSession(
            headless=options.headless,
            network_idle=options.network_idle,
            max_pages=max(1, options.concurrency),
            proxy=proxy,
        )
    if options.fetcher_mode == FetcherMode.STEALTH_BROWSER:
        return StealthySession(
            headless=options.headless,
            solve_cloudflare=options.solve_cloudflare,
            max_pages=max(1, options.concurrency),
            proxy=proxy,
        )
    raise RuntimeError(f"وضع جلب غير معروف: {options.fetcher_mode}")


def extract_links(page: Any, base_url: str, same_domain_only: bool) -> list[str]:
    """Pull every <a href> from a fetched page, for the 'Follow links'
    crawl option. Domain filtering happens here so the adapter stays the
    single place that understands Scrapling's Selector output shape."""
    from urllib.parse import urljoin, urlparse

    hrefs = page.css("a::attr(href)").getall()
    base_host = urlparse(base_url).netloc
    out = []
    for href in hrefs:
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href)
        if same_domain_only and urlparse(absolute).netloc != base_host:
            continue
        out.append(absolute)
    return out


def get_html(page: Any) -> str:
    """
    Best-effort raw HTML extraction from a fetched page, used by the lead
    qualifier (app/core/engine/qualifier.py) which needs the full document
    rather than a specific selector's match.

    Scrapling's Selector/Response object isn't guaranteed to expose the
    same attribute name across versions, so this tries the documented/
    common ones in order rather than assuming one - a wrong guess here
    should degrade to "no signal" (empty string), never crash the job.
    """
    for attr in ("html_content", "html", "body", "text", "content"):
        value = getattr(page, attr, None)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, bytes) and value.strip():
            try:
                return value.decode("utf-8", errors="ignore")
            except Exception:
                continue
    try:
        root = page.css("html")
        if root:
            return root[0].html or ""
    except Exception:
        pass
    return ""


class _TextExtractor(HTMLParser):
    """stdlib-only HTML-to-visible-text converter (no bs4/lxml dependency
    needed just for this) - used to turn a fetched page into plain text
    for the AI Auto-Extract mode (app/core/engine/ai_extractor.py)."""

    _SKIP_TAGS = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0:
            text = data.strip()
            if text:
                self._chunks.append(text)

    def get_text(self) -> str:
        return "\n".join(self._chunks)


def html_to_text(html: str) -> str:
    """Strip tags/scripts/styles down to the visible text a human reading
    the page would see - this is what gets sent to the LLM in AI
    Auto-Extract mode, not raw HTML (cheaper, and the model does better
    on clean text than on markup soup)."""
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.get_text()
