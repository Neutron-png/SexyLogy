"""
The ONLY module in LOGY allowed to import the fetch engine directly.

Every other layer (job manager, UI, storage) talks to the scraping engine
through the functions below, never to `scrapling` itself. That is the
"UI -> Job Manager -> Scraping Engine -> the fetch engine" boundary from the
architecture doc: it lets the rest of the app be unit-tested without
the fetch engine installed, and it means if the engine's API changes, only this
file needs to change.

API surface used here is exactly what the fetch engine documents
(https://github.com/D4Vinci/the fetch engine):

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
import os
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

from app.core.engine import anonymity
from app.core.engine import brain as brain_mod
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
    ENGINE_AVAILABLE = True
    ENGINE_IMPORT_ERROR = None
except Exception as e:  # ImportError, or a missing browser binary raising on import
    ENGINE_AVAILABLE = False
    ENGINE_IMPORT_ERROR = str(e)


class FetchError(Exception):
    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"{url}: {reason}")


def _apply_persona_kwargs(fetcher_mode: FetcherMode, options: ScrapeOptions) -> dict:
    """Identity persona → engine kwargs. Browser engines take useragent/
    locale/timezone_id natively; FAST_HTTP gets the UA via headers (curl-cffi
    generates its own browser headers for impersonation, so an explicit UA
    must ride in the header dict, not a dedicated kwarg)."""
    persona = options.persona or {}
    if not persona:
        return {}
    if fetcher_mode == FetcherMode.FAST_HTTP:
        return {"headers": {"User-Agent": persona["useragent"], **(options.headers or {})}}
    kwargs: dict = {}
    if persona.get("useragent"):
        kwargs["useragent"] = persona["useragent"]
    if persona.get("locale"):
        kwargs["locale"] = persona["locale"]
    if persona.get("timezone_id"):
        kwargs["timezone_id"] = persona["timezone_id"]
    return kwargs


def probe_proxies(proxies: list[str], timeout_s: float = 8.0) -> tuple[list[str], list[str]]:
    """Pre-flight parallel health probe: one cheap request per proxy before
    the job starts, so dead entries are removed from the pool at t=0 instead
    of being discovered mid-run as mystery failures. Returns (alive, dead)."""
    def _probe(proxy: str) -> tuple[str, bool]:
        try:
            Fetcher.get("https://example.com", proxy=proxy, timeout=timeout_s, retries=0)
            return proxy, True
        except Exception:
            return proxy, False

    alive, dead = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(proxies)))) as pool:
        for proxy, ok in pool.map(_probe, proxies):
            (alive if ok else dead).append(proxy)
    return alive, dead


class FetchCancelled(FetchError):
    """Raised when should_stop() flips true mid-fetch - lets the job
    manager tell 'user hit Stop' apart from a real network failure, so
    it can exit the retry loop immediately instead of treating it as
    just another failed attempt to retry."""


@dataclass
class FetchResult:
    url: str
    page: Any            # a the fetch engine Selector-compatible page object
    status: Optional[int]
    ok: bool


def _normalize_tor_scheme(proxy_str: str, for_http: bool) -> str:
    """Hybrid mode mixes user proxies with the Tor endpoint in ONE pool, so
    a plain 'socks5://127.0.0.1:<port>' can land on any engine. This keeps
    each engine's scheme requirement satisfied: socks5h (remote DNS) for
    curl-cffi, socks5 for the Playwright browsers - and passes everything
    else through untouched."""
    if proxy_str.startswith("socks5://127.0.0.1:"):
        return anonymity.tor_socks_url(int(proxy_str.rsplit(":", 1)[1]), for_http=for_http)
    return proxy_str


def _proxy_kwarg(proxy: ProxyConfig, for_http: bool) -> Optional[str]:
    """the engine's fetchers accept a single `proxy=` string per request.
    Rotation across a list is handled by the job manager picking a
    different entry per request and passing it back via proxy.proxies[0].

    mode == "tor": tunnel through the local Tor daemon. Scheme depends on
    the engine - socks5h (remote DNS) for curl-cffi, plain socks5 for the
    Playwright-based engines (their validator rejects socks5h). See
    app/core/engine/anonymity.py for why the schemes differ.
    mode == "hybrid": the job manager put either a user proxy OR the Tor
    endpoint in proxies[0] - normalize the Tor scheme per engine here."""
    if proxy.mode == "none":
        return None
    if proxy.mode == "tor":
        return anonymity.tor_socks_url(proxy.tor_socks_port, for_http=for_http)
    if not proxy.proxies:
        return None
    if proxy.mode == "single":
        return proxy.proxies[0]
    # "list" / "rotating" / "hybrid": caller (job manager) selects the
    # entry and passes it back in via proxy.proxies[0] for this call.
    return _normalize_tor_scheme(proxy.proxies[0], for_http)


def _is_tor(proxy: ProxyConfig) -> bool:
    if proxy.mode == "tor":
        return True
    # hybrid: WebRTC blocking is needed only for the requests that are
    # actually tunneling through Tor this time, not the plain-proxy ones
    return bool(proxy.mode == "hybrid" and proxy.proxies and proxy.proxies[0].startswith("socks5://127.0.0.1:"))


def _cookie_header(cookies: dict) -> str:
    """Serialize a cookie dict to a Cookie header value - the one cookie
    mechanism that works on EVERY engine (the engine's fast HTTP fetcher
    has no cookies= kwarg at all)."""
    items = [(k, v) for k, v in (cookies or {}).items() if v not in (None, "")]
    return "; ".join(f"{k}={v}" for k, v in items)


def _merged_headers(options: ScrapeOptions) -> dict:
    """options.headers + the Cookie header baked from options.cookies.
    Used by every FAST_HTTP path; browser engines get cookies natively."""
    headers = dict(options.headers or {})
    cookie = _cookie_header(options.cookies)
    if cookie:
        headers.setdefault("Cookie", cookie)
    return headers


def _browser_profile_dir(mode: FetcherMode) -> Optional[str]:
    """Stable per-engine profile directory so the browser's cookies and
    localStorage survive across jobs (the 'returning visitor' effect -
    a site that set a clearance cookie last week sees a known visitor,
    not a brand-new one). None when the OS refuses the directory."""
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    d = Path(base) / "LOGY" / "browser_profiles" / mode.value
    try:
        d.mkdir(parents=True, exist_ok=True)
        return str(d)
    except OSError:
        return None


def require_engine():
    if not ENGINE_AVAILABLE:
        raise RuntimeError(
            "the fetch engine غير مثبت أو ناقصه اعتمادية (browser binaries). "
            f"تفاصيل: {ENGINE_IMPORT_ERROR}. "
            "شغّل: pip install \"scrapling[fetchers]\" && scrapling install"
        )


def engine_diagnostics() -> dict:
    """Mirror of the engine's official optional-dependencies install
    (docs → Installation → Optional Dependencies): the fetchers extra
    (playwright + curl-cffi) and the browser binaries `scrapling install`
    downloads. The sidebar surfaces this so a fresh machine can one-click
    repair instead of dying on the first fetch."""
    import importlib.util
    import os as _os

    diag = {"engine": ENGINE_AVAILABLE, "fetchers_deps": False,
            "browsers": False, "detail": ENGINE_IMPORT_ERROR or ""}
    try:
        pw = importlib.util.find_spec("playwright") is not None
        cc = importlib.util.find_spec("curl_cffi") is not None
        diag["fetchers_deps"] = bool(pw and cc)
    except Exception:
        diag["fetchers_deps"] = False
    if diag["fetchers_deps"]:
        try:
            # fast silent check: scan the standard ms-playwright cache for
            # a chromium build (avoids spawning the playwright driver just
            # to read a path)
            exe_candidates = []
            local = _os.environ.get("LOCALAPPDATA") or _os.path.expanduser("~/.local")
            base = Path(local) / "ms-playwright"
            if base.exists():
                exe_candidates = [str(p) for p in base.glob("chromium-*/chrome-win*/chrome.exe")]
            diag["browsers"] = any(_os.path.exists(e) for e in exe_candidates)
            if not diag["browsers"]:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    exe = p.chromium.executable_path
                    diag["browsers"] = bool(exe and _os.path.exists(exe))
        except Exception as e:
            diag["browsers"] = False
            diag["detail"] = f"browser check: {e}"
    diag["ok"] = bool(diag["engine"] and diag["fetchers_deps"] and diag["browsers"])
    return diag


def install_engine_deps(log_cb=None) -> bool:
    """Run the documented installer from inside LOGY (docs: 'you can
    install them from the code' → scrapling.cli.install). Two steps, both
    streamed to `log_cb(line)`: 1) pip install the fetchers extra,
    2) `scrapling install` to download browsers + system deps. Never
    raises - returns True when diagnostics report healthy afterwards."""
    import subprocess
    import sys as _sys

    def say(line: str):
        if log_cb:
            try:
                log_cb(line.rstrip())
            except Exception:
                pass

    steps = [
        [_sys.executable, "-m", "pip", "install", "--quiet", "scrapling[fetchers]"],
        [_sys.executable, "-c",
         "from scrapling.cli import install; install([], standalone_mode=False)"],
    ]
    ok = True
    for cmd in steps:
        say(f"$ {' '.join(cmd[1:4])} ...")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
            for line in proc.stdout or []:
                say(line)
            proc.wait()
            if proc.returncode != 0:
                ok = False
                say(f"step failed with code {proc.returncode}")
        except Exception as e:
            ok = False
            say(f"installer error: {e}")
    diag = engine_diagnostics()
    say(f"engine diagnostics after install: ok={diag['ok']}")
    return bool(ok and diag["ok"])


# scrapling.fetchers.Fetcher.get() is a classmethod that delegates to one
# module-level singleton instance the fetch engine creates at import time
# (confirmed by reading the engine's own source, scrapling/fetchers/requests.py:
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
# instead of a fresh one per call. That guarantees the engine's fetchers
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
            max_workers=1, thread_name_prefix="LOGY-fetch"
        )
    return _fetch_executor


class _CachedPage:
    """A zero-network page: wraps a Selector built from cached HTML so every
    downstream consumer (extractor.css/xpath, extract_links, get_html) works
    unchanged on a cache hit. status 200 by fiat - the content IS the page."""

    def __init__(self, html: str, url: str):
        from scrapling.parser import Selector
        self._sel = Selector(content=html, url=url)
        self._html = html
        self.status = 200

    def css(self, *a, **k):
        return self._sel.css(*a, **k)

    def xpath(self, *a, **k):
        return self._sel.xpath(*a, **k)

    @property
    def html(self) -> str:
        return self._html


def _header_value(headers: Any, name: str) -> str:
    try:
        for k, v in dict(headers or {}).items():
            if k.lower() == name.lower():
                return str(v)
    except Exception:
        pass
    return ""


def fetch_one(url: str, options: ScrapeOptions, should_stop=None, cache: Optional[brain_mod.Brain] = None,
              wait_selector: Optional[str] = None, session: Optional[Any] = None) -> FetchResult:
    """
    Fetch a single URL using the fetcher mode selected in Step 3 of the
    New Scrape wizard. Raises FetchError on failure (timeout, connection
    error, HTTP error, blocked request) - the job manager turns that into
    a per-URL job error rather than crashing the run.

    `cache` (optional, FAST_HTTP only): a Brain carrying the response cache.
    Fresh entries return a zero-network _CachedPage; stale ones revalidate
    with If-None-Match/If-Modified-Since and a 304 is served from cache.

    `wait_selector` (optional, browser modes only): CSS selector the browser
    waits to see attached before snapshotting - required to see through
    self-solving challenges (AWS WAF) and hydrating JS listings.

    `session` (optional): an open persistent-session handle from
    open_session() - one browser / connection pool per JOB instead of one
    per page, so cookies and state survive across requests (the official
    Session Support capability). When given, the per-request proxy chosen
    by the identity brain is still honored via the session's proxy param.
    """
    require_engine()
    proxy = _proxy_kwarg(options.proxy, for_http=(options.fetcher_mode == FetcherMode.FAST_HTTP))
    persona_kwargs = _apply_persona_kwargs(options.fetcher_mode, options)
    use_cache = (cache is not None and options.use_response_cache
                 and options.fetcher_mode == FetcherMode.FAST_HTTP)

    # ---- cache fast path: fresh entry = zero requests ----
    if use_cache:
        hit = cache.cache_get(url, options.cache_ttl_s)
        if hit is not None:
            html, cond_headers = hit
            if not cond_headers:
                return FetchResult(url=url, page=_CachedPage(html, url), status=200, ok=True)
            options.headers = {**(options.headers or {}), **cond_headers}

    def _do_fetch():
        if session is not None:
            # persistent-session path: one browser/pool per job, per-request
            # proxy override keeps the identity brain in charge
            return session.fetch(url, options, wait_selector=wait_selector,
                                 proxy_override=proxy)
        if options.fetcher_mode == FetcherMode.FAST_HTTP:
            return Fetcher.get(
                url,
                timeout=options.timeout_s,
                # the fetch engine 0.4.x interprets retries=0 as zero loop
                # iterations and raises "No active session available"
                # without issuing a request. The job manager owns the
                # retry/backoff loop, so each call must make at least one
                # real HTTP attempt even when the user disables retries.
                retries=max(1, options.retries),
                proxy=proxy,
                headers=_merged_headers(options) or None,
                stealthy_headers=True,
                **persona_kwargs,
            )
        elif options.fetcher_mode == FetcherMode.DYNAMIC_BROWSER:
            dynamic_kwargs = {}
            if wait_selector:
                dynamic_kwargs["wait_selector"] = wait_selector
                dynamic_kwargs["wait_selector_state"] = "attached"
            return DynamicFetcher.fetch(
                url,
                headless=options.headless,
                network_idle=options.network_idle,
                disable_resources=options.disable_resources,
                timeout=eff_timeout * 1000,
                proxy=proxy,
                **dynamic_kwargs,
                **persona_kwargs,
            )
        elif options.fetcher_mode == FetcherMode.STEALTH_BROWSER:
            stealth_kwargs = {}
            if wait_selector:
                # AWS-WAF-style challenges serve a JS shell that solves
                # itself and reloads; waiting for the page's real content
                # selector is what turns the challenge into a 200 listing
                # page (verified live on thumbtack.com).
                stealth_kwargs["wait_selector"] = wait_selector
                stealth_kwargs["wait_selector_state"] = "attached"
            profile_dir = _browser_profile_dir(options.fetcher_mode) if options.browser_profile else None
            return StealthyFetcher.fetch(
                url,
                headless=options.headless,
                network_idle=options.network_idle,
                solve_cloudflare=options.solve_cloudflare,
                # Through Tor, WebRTC would happily dial STUN servers DIRECTLY
                # (bypassing the SOCKS tunnel) and hand the site the machine's
                # real IP - block it whenever we're tunneling. Stealth engine
                # supports this flag; the plain Dynamic engine doesn't.
                block_webrtc=_is_tor(options.proxy),
                real_chrome=options.real_chrome,
                dns_over_https=options.dns_over_https,
                block_ads=options.block_ads,
                cookies=options.cookies or None,
                extra_headers=options.headers or None,
                user_data_dir=profile_dir,
                cdp_url=options.cdp_url or None,
                timeout=options.timeout_s * 1000,
                proxy=proxy,
                **stealth_kwargs,
                **persona_kwargs,
            )
        else:
            raise FetchError(url, f"وضع جلب غير معروف: {options.fetcher_mode}")

    # Hard watchdog: some targets (heavy anti-bot walls, dead proxies, a
    # firewall silently dropping packets) can make the underlying network
    # call hang far longer than the `timeout=` kwarg we pass the fetch engine -
    # that kwarg only bounds the engine's *own* wait, not every possible
    # hang below it (TCP connect stalls, a browser process that never
    # responds, etc). Running the call in the persistent single-worker
    # executor (see _get_fetch_executor() above) and bounding it with
    # `future.result(timeout=...)` here guarantees LOGY itself never waits
    # forever on one URL, regardless of what the fetch engine/the network does -
    # while still only ever using that one stable worker thread, never a
    # freshly spawned one, to avoid racing the engine's shared fetcher
    # singleton (see the long comment above _get_fetch_executor()).
    # Browser fetches that wait on a real-content selector need a bigger
    # budget than the UI's default 20s: the AWS-WAF challenge (~8s), the
    # reload, and a possible category redirect all happen BEFORE the
    # selector can appear. Floored at 120s so the user's short timeout
    # doesn't guarantee the challenge wins every time (job 16's symptom:
    # stealth ran, timed out at 20s, returned the shell, looked "blocked").
    eff_timeout = options.timeout_s
    if wait_selector:
        eff_timeout = max(options.timeout_s, 120)
    watchdog_seconds = eff_timeout + 15  # a little slack over the engine's own timeout
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

    # Session path: the handle already unwrapped its inner Response and
    # validated the status - it returned a complete FetchResult.
    if isinstance(page, FetchResult):
        return page

    status = getattr(page, "status", None)
    ok = status is None or (200 <= int(status) < 400)
    if not ok:
        raise FetchError(url, f"HTTP {status}")

    # ---- cache store / revalidate ----
    if use_cache:
        status_int = int(status) if status is not None else 200
        if status_int == 304:
            # revalidated: server says unchanged - serve the cached body
            hit = cache.cache_get(url, 10**12)   # force the stale-but-present path
            if hit is not None:
                return FetchResult(url=url, page=_CachedPage(hit[0], url), status=200, ok=True)
        elif status_int == 200 and cache is not None:
            html = get_html(page)
            if html:
                headers = getattr(page, "headers", None)
                cache.cache_put(
                    url, html,
                    etag=_header_value(headers, "ETag"),
                    last_modified=_header_value(headers, "Last-Modified"))

    return FetchResult(url=url, page=page, status=status, ok=ok)


class _SessionHandleBase:
    """Wraps a persistent the fetch engine session with the thread-affinity rule
    the whole engine lives by: open/fetch/close all hop through the ONE
    persistent fetch executor thread (playwright sync objects are bound to
    the thread that created them - see _get_fetch_executor's docstring)."""

    def __init__(self, session_obj):
        self._session = session_obj

    def _run(self, fn):
        future = _get_fetch_executor().submit(fn)
        return future.result(timeout=self._close_timeout_s())

    def _close_timeout_s(self) -> float:
        return 60.0

    def close(self):
        session = self._session

        def _close():
            try:
                session.__exit__(None, None, None)
            except Exception:
                pass

        try:
            self._run(_close)
        except Exception:
            pass


class _FastSessionHandle(_SessionHandleBase):
    """FetcherSession handle: TLS/connection + cookie reuse for fast HTTP."""

    def __init__(self, options: ScrapeOptions):
        proxy = _proxy_kwarg(options.proxy, for_http=True)
        session = FetcherSession(
            impersonate="chrome",
            stealthy_headers=True,
            timeout=options.timeout_s,
            retries=max(1, options.retries),
            proxy=proxy,
            http3=options.real_chrome,  # HTTP/3 rides with the real-browser extras
        )
        super().__init__(session)
        self._client = None

    def _close_timeout_s(self) -> float:
        return 30.0

    def enter(self):
        # __enter__ returns the live _SyncSessionLogic client (the object
        # that actually has .get()); keep it - not the wrapper
        self._client = self._run(lambda: self._session.__enter__())

    def fetch(self, url: str, options: ScrapeOptions, wait_selector=None,
              proxy_override: Optional[str] = None) -> FetchResult:
        # ALWAYS called from inside _do_fetch, i.e. already on the fetch
        # executor thread - calling _run() here would self-deadlock the
        # single-worker executor (a submit waiting on itself).
        if self._client is None:
            raise FetchError(url, "الجلسة مش مفتوحة")
        proxy = _normalize_tor_scheme(proxy_override, for_http=True) if proxy_override else None
        inner = self._client.get(
            url,
            proxy=proxy,
            headers=_merged_headers(options) or None,
        )
        # the session client returns the full the fetch engine Response directly
        # (Response IS a Selector - html_content/body/css all live on it)
        page = inner
        status = getattr(inner, "status", None)
        ok = getattr(inner, "ok", status is None or (200 <= int(status or 200) < 400))
        if not ok:
            raise FetchError(url, f"HTTP {status}")
        return FetchResult(url=url, page=page, status=int(status) if status is not None else 200, ok=ok)


class _BrowserSessionHandle(_SessionHandleBase):
    """StealthySession/DynamicSession handle: ONE browser per job - pages
    reuse the same context, cookies survive across fetches, and every
    fetch still carries the identity brain's chosen proxy via the
    per-fetch `proxy=` override the official API provides."""

    _close_timeout_s_value = 60.0

    def __init__(self, options: ScrapeOptions, mode: FetcherMode):
        proxy = _proxy_kwarg(options.proxy, for_http=False)
        profile_dir = _browser_profile_dir(mode) if options.browser_profile else None
        if mode == FetcherMode.STEALTH_BROWSER:
            session = StealthySession(
                headless=options.headless,
                solve_cloudflare=options.solve_cloudflare,
                block_webrtc=_is_tor(options.proxy),
                real_chrome=options.real_chrome,
                cookies=options.cookies or None,
                extra_headers=options.headers or None,
                user_data_dir=profile_dir,
                cdp_url=options.cdp_url or None,
                proxy=proxy,
            )
        else:
            session = DynamicSession(
                headless=options.headless,
                network_idle=options.network_idle,
                max_pages=max(1, options.concurrency),
                user_data_dir=profile_dir,
                cdp_url=options.cdp_url or None,
                proxy=proxy,
            )
        super().__init__(session)
        self._mode = mode

    def _close_timeout_s(self) -> float:
        return self._close_timeout_s_value

    def enter(self):
        self._run(lambda: self._session.__enter__())

    def fetch(self, url: str, options: ScrapeOptions, wait_selector=None,
              proxy_override: Optional[str] = None) -> FetchResult:
        # Called from inside _do_fetch (fetch executor thread) - direct
        # call, no re-submit (would self-deadlock the single worker).
        kwargs: dict[str, Any] = {"timeout": max(options.timeout_s, 20) * 1000}
        if wait_selector:
            kwargs["wait_selector"] = wait_selector
            kwargs["wait_selector_state"] = "attached"
        if options.disable_resources:
            kwargs["disable_resources"] = True
        if options.block_ads:
            kwargs["block_ads"] = True
        if proxy_override:
            kwargs["proxy"] = _normalize_tor_scheme(proxy_override, for_http=False)
        page = self._session.fetch(url, **kwargs)
        status = getattr(page, "status", None)
        return FetchResult(url=url, page=page, status=int(status) if status is not None else None,
                           ok=status is None or (200 <= int(status) < 400))


def open_session(options: ScrapeOptions, mode: FetcherMode):
    """Create (unentered) the persistent session handle for `mode`. The
    worker calls handle.enter() on the fetch thread and handle.close() when
    the job ends. Returns None for unsupported modes so callers fall back
    to per-page fetches."""
    require_engine()
    if mode == FetcherMode.FAST_HTTP:
        return _FastSessionHandle(options)
    if mode in (FetcherMode.STEALTH_BROWSER, FetcherMode.DYNAMIC_BROWSER):
        return _BrowserSessionHandle(options, mode)
    return None


def make_session(options: ScrapeOptions):
    """
    Build a persistent session object for a multi-page crawl, so LOGY
    reuses one browser/connection instead of paying startup cost per page
    (this backs the "Follow links" / pagination options in Step 1 & 3).
    Caller is responsible for using it as a context manager.
    """
    require_engine()
    proxy = _proxy_kwarg(options.proxy, for_http=(options.fetcher_mode == FetcherMode.FAST_HTTP))

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
            block_webrtc=_is_tor(options.proxy),
            max_pages=max(1, options.concurrency),
            proxy=proxy,
        )
    raise RuntimeError(f"وضع جلب غير معروف: {options.fetcher_mode}")


def extract_links(page: Any, base_url: str, same_domain_only: bool) -> list[str]:
    """Pull every <a href> from a fetched page, for the 'Follow links'
    crawl option. Domain filtering happens here so the adapter stays the
    single place that understands the engine's Selector output shape."""
    from urllib.parse import urljoin, urlparse

    hrefs = page.css("a::attr(href)").getall()
    base_host = urlparse(base_url).netloc
    out = []
    for href in hrefs:
        if not href or href.startswith(("javascript:", "mailto:", "#")):
            continue
        absolute = urljoin(base_url, href)
        # Only http(s) is followable - a scraped page must not steer the
        # crawler into file:// or other local-scheme URLs (audit §10).
        if urlparse(absolute).scheme.lower() not in ("http", "https"):
            continue
        if same_domain_only and urlparse(absolute).netloc != base_host:
            continue
        out.append(absolute)
    return out


def get_html(page: Any) -> str:
    """
    Best-effort raw HTML extraction from a fetched page, used by the lead
    qualifier (app/core/engine/qualifier.py) which needs the full document
    rather than a specific selector's match.

    the engine's Selector/Response object isn't guaranteed to expose the
    same attribute name across versions, so this tries the documented/
    common ones in order rather than assuming one - a wrong guess here
    should degrade to "no signal" (empty string), never crash the job.
    """
    for attr in ("html_content", "html", "body", "text", "content"):
        value = getattr(page, attr, None)
        if value is None:
            continue
        if isinstance(value, bytes) and value.strip():
            try:
                return value.decode("utf-8", errors="ignore")
            except Exception:
                continue
        if isinstance(value, str) and value.strip():
            return value
        # str-like containers (e.g. the engine's TextHandler returned by
        # session fetches) that are not str subclasses but are len-able
        if not isinstance(value, (str, bytes)) and hasattr(value, "__len__"):
            try:
                as_text = str(value)
            except Exception:
                continue
            if as_text.strip():
                return as_text
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