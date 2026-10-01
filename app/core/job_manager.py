"""
Job Manager: owns the background thread that actually runs a scrape, and
is the only bridge between the Qt UI and the (Qt-free) engine layer.

Architecture (spec section 12):
    UI -> JobManager -> ScrapeJobWorker -> fetch_engine -> the fetch engine
                                        -> extractor
                                        -> Database (streamed results)

Runs on a QThread so the GUI event loop is never blocked by network I/O
or a browser call (spec section 13/29: "no UI freezing").
"""
from __future__ import annotations

import hashlib
import re
import time
import traceback
from collections import deque
from dataclasses import asdict
from typing import Optional
from urllib import robotparser
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Signal

from app.core.engine import fetch_engine as engine
from app.core.engine import ai_extractor
from app.core.engine import anonymity
from app.core.engine import proxy_feed
from app.core.engine import brain as brain_mod
from app.core.engine.dedupe import fingerprint_lead
from app.core.engine.extractor import extract_fields, extract_records, ExtractionError
from app.core.engine.qualifier import qualify_html
from app.core.models import ExtractionField, FetcherMode, JobStatus, TargetConfig, ScrapeOptions, LogLevel, ProxyConfig, AIExtractionConfig
from app.core.storage.db import Database
from app.core.storage.secrets import SecretStore, redact_secrets


class ScrapeJobWorker(QObject):
    log = Signal(str, str)                       # level, message
    progress = Signal(int, int, int, int)         # pages_done, pages_total, records_ok, records_failed
    result_ready = Signal(dict)                   # one extracted record (already persisted)
    url_error = Signal(str, str)                  # url, reason
    status_changed = Signal(str)                  # JobStatus value
    finished = Signal(int)                        # job_id

    def __init__(self, db: Database, job_id: int, project_id: Optional[int],
                 target: TargetConfig, fields: list[ExtractionField],
                 options: ScrapeOptions, container: Optional[dict] = None,
                 detail_config: Optional[dict] = None,
                 source_profiles: Optional[list[dict]] = None,
                 resumed: bool = False):
        super().__init__()
        self.db = db
        self.job_id = job_id
        self.project_id = project_id
        self.target = target
        self.fields = fields
        self.options = options
        self.container = container  # {"selector": ..., "type": "css"|"xpath"} for repeating listings
        # Optional second-fetch enrichment for sources whose listing page
        # doesn't carry every field (e.g. yelp.com's search results have
        # no phone number - only the business's own page does). Shape:
        # {"link_field": "<record key holding a URL>",
        #  "fields": [ExtractionField, ...],           # CSS-based, optional
        #  "regex_fields": {"phone": r"..."}}           # regex-based, optional
        # See _enrich_with_detail_page() below and
        # app/core/engine/builtin_templates.py's _YELP_DETAIL_CONFIG.
        self.detail_config = detail_config

        # Multi-source combined run (see new_scrape.py's "Load All
        # Sources" button and Quick Start's "All Sources" niche option -
        # "عايز كل اللينكات الممكنة في وقت واحد مش يمشي عليها واحد واحد").
        # A single job's start_urls can now mix URLs from more than one
        # site (e.g. yellowpages.com + yelp.com) in the SAME list, because
        # applying one site's container/field selectors to a different
        # site's markup would silently extract nothing (or garbage) for
        # whichever site the selectors weren't built for. source_profiles,
        # when set, is a list of
        # {"name": ..., "domain": ..., "container": ..., "fields": [...],
        #  "detail_config": ...} dicts (see builtin_templates.SOURCE_
        # PROFILES) - _resolve_source() below picks the right one per URL
        # by matching its domain, right before that page is extracted.
        # self.container/self.fields/self.detail_config above stay as the
        # fallback used for any URL whose domain matches none of the
        # profiles (and are exactly what's used, unchanged, when
        # source_profiles is None - the original single-source behavior).
        self.source_profiles = source_profiles
        self._base_container = container
        self._base_fields = fields
        self._base_detail_config = detail_config

        # Anonymity/rotation state (see _prepare_proxy() below):
        # - the untouched proxy list exactly as the user entered it
        # - a per-request counter (drives Tor's NEWNYM circuit rotation)
        # - a lazy CyclicProxyRotator for "list"/"rotating" modes
        self._proxy_pool: list[str] = list(options.proxy.proxies)
        self._rotator = None
        self._request_seq = 0

        # Intelligence layer (app/core/engine/brain.py): cross-run identity
        # reputation + per-domain pacing + WAF breaker + response cache.
        # Created lazily in run() only when at least one feature is on,
        # so a plain job touches none of this.
        self._brain: Optional[brain_mod.Brain] = None
        self._pacer: Optional[brain_mod.BurstPacer] = None
        self._requeues: dict[str, int] = {}   # parked-domain requeue guard
        self._rotated_hosts: set[str] = set()  # hosts given ONE identity-rotation rescue per job
        # WAF-rescue memory (per job): hosts whose fast-HTTP fetch comes
        # back as a challenge shell. After one successful stealth rescue
        # the host's remaining URLs go straight to the stealth browser
        # (skipping the wasted shell fetch); after a failed rescue the
        # host is marked dead so we don't burn ~30s per URL on retries.
        self._force_browser_hosts: set[str] = set()
        self._browser_dead_hosts: set[str] = set()
        # Hosts whose fast-HTTP lane proved BLOCKED (403/429...) and were
        # handed to the stealth browser ONCE this job. Distinct from
        # _force_browser_hosts so a failed stealth handover doesn't get
        # re-attempted forever through the shell-rescue path.
        self._stealth_escalated_hosts: set[str] = set()
        self._mode_options: dict = {}
        self._robots_cache: dict = {}
        self._raw_by_alias: dict[str, str] = {}
        self._ai_calls = 0
        self._ai_budget_warned = False
        self.resumed = resumed
        # Persistent-session handles, one per engine mode (audit H2): one
        # browser per stealth run instead of one per page; cookies/state
        # survive across pages (official Session Support capability).
        self._sessions: dict = {}
        # Adaptive-selector tracking (the fetch engine auto_save/adaptive): first
        # successful page per host saves the container fingerprint; a
        # zero-record page later retries with adaptive relocation.
        self._adaptive_saved_hosts: set[str] = set()

        self._stop_requested = False
        self._pause_requested = False

    # --- control (called from the UI thread via queued connections) ---
    def request_stop(self):
        self._stop_requested = True

    def request_pause(self, paused: bool):
        self._pause_requested = paused
        self.status_changed.emit(JobStatus.PAUSED.value if paused else JobStatus.RUNNING.value)

    # --- main loop ---
    def run(self):
        # Everything is inside this one try/except/finally now, including
        # the very first status/log emit. Previously that first emit sat
        # OUTSIDE the try block - if it raised (which it did in practice:
        # self.db was a sqlite3 connection opened on the GUI thread, and
        # touching it from this worker thread raised ProgrammingError
        # immediately, see Database's docstring in storage/db.py), the
        # exception propagated straight out of run() and skipped the
        # `finally: self.finished.emit(...)` below entirely. That left the
        # QThread never told to quit(), so it just sat there forever:
        # Stop did nothing (nothing was polling _stop_requested anymore),
        # the progress bar never moved, and the log panel stayed empty -
        # the whole run() body is wrapped now so ANY failure, from any
        # cause, still reaches finished.emit() and the job is reported as
        # FAILED instead of hanging silently.
        try:
            self.status_changed.emit(JobStatus.RUNNING.value)
            self._emit_log(LogLevel.INFO, "بدء عملية الاستخراج")
            self._check_tor_at_start()
            self._setup_brain()
            self._probe_proxies_at_start()
            self._expand_sitemap()

            queue: deque[tuple[str, int]] = deque((u, 0) for u in self.target.start_urls)  # (url, depth)
            seen: set[str] = set(self.target.start_urls)
            pages_done = 0
            records_ok = 0
            records_failed = 0
            duplicates_skipped = 0  # leads whose fingerprint was already in lead_history (see dedupe.py)
            # Honest progress denominator: the actual known work (done +
            # still queued), NOT target.max_pages. The old
            # `max(len(queue), target.max_pages)` put 100,000 in the
            # denominator for a 3-URL run, so the UI bar sat at 0% for the
            # whole job. Discovered links (follow_links) grow it naturally.
            pages_total = max(len(queue), 1)
            first_page = True  # no delay before the very first fetch

            # Checkpoint the queue (audit C3/H1): fresh starts write the
            # full known work; resumes keep existing done/failed rows and
            # only the pending set was passed in as start_urls.
            if not self.resumed:
                self.db.queue_replace(self.job_id, list(queue))
            else:
                self.db.queue_reset_in_progress(self.job_id)

            def emit_progress():
                # One progress shape for every emit site: the denominator
                # is the ACTUAL known work (finished + still queued), so
                # the UI bar reflects reality instead of sitting at 0%
                # against a hypothetical max_pages ceiling.
                total = max(pages_done + len(queue), 1)
                self.progress.emit(pages_done, total, records_ok, records_failed)

            while queue and not self._stop_requested:
                while self._pause_requested and not self._stop_requested:
                    time.sleep(0.2)
                if self._stop_requested:
                    break
                if pages_done >= self.target.max_pages:
                    self._emit_log(LogLevel.INFO, f"تم الوصول للحد الأقصى للصفحات ({self.target.max_pages})")
                    break

                # Per-request identity + pacing need the URL's host, so the
                # pop happens FIRST now (the old code slept before popping -
                # fine for a flat global delay, wrong for per-domain AIMD).
                # The historical bug context: "delay between requests" was
                # collected from the UI but never used anywhere in this loop
                # (a 500-page Yelp run hammered yelp.com back-to-back - a big
                # part of why its WAF returned HTTP 403 on nearly every
                # request after the first few dozen; the 91/91-failed run).
                url, depth = queue.popleft()
                self.db.queue_mark(self.job_id, url, "in_progress")

                # Pacing: with the brain active, the per-domain LEARNED delay
                # (AIMD) and/or the human-burst pacer own this sleep instead
                # of the flat options.delay_ms. Runs on every iteration so a
                # page that just got 403'd doesn't get hammered again next.
                if not first_page and not self._pacing_sleep(url):
                    self._interruptible_sleep(self.options.delay_ms / 1000.0)
                first_page = False
                if self._stop_requested:
                    break  # Stop was clicked during the delay itself

                # Circuit breaker: a domain whose WAF-pressure estimate went
                # red is PARKED - requeue its URLs for after the park window
                # instead of burning the identity pool against a wall.
                if self._brain is not None and not self._brain.gate(url):
                    # Host-level (not per-URL) deferral cap: the old per-URL
                    # guard let a pre-expanded pagination queue (page=2..10)
                    # of a hard-blocked host keep the job "alive" with a
                    # 30s-sleep + defer log cycle for many minutes while
                    # producing nothing (the "9 دقايق من غير ليد" run -
                    # every URL was yelp/yellowpages 403s being parked).
                    # After 2 deferrals the host's REMAINING queued URLs are
                    # dropped for this job and the loop moves on / ends.
                    host = brain_mod.host_of(url)
                    self._requeues[host] = self._requeues.get(host, 0) + 1

                    # Fight before flight #1 - the stealth browser: the fast
                    # lane just proved BLOCKED for this host, and scrapling's
                    # stealth engine is exactly what passes those walls (same
                    # escalation the WAF-shell rescue uses below). Hand the
                    # host's URLs to the browser immediately instead of
                    # deferring them through 30s-sleep cycles that produce
                    # nothing (the yellowpages 403 session: minutes of
                    # "تأجيل" logs, zero leads, then everything dropped).
                    if self._stealth_escalation_possible(url, host):
                        self._stealth_escalated_hosts.add(host)
                        self._force_browser_hosts.add(host)
                        self._requeues[host] = 0
                        # The browser identity gets a clean slate - the
                        # parked reputation belongs to the fast lane.
                        self._brain.unlock(host)
                        self._emit_log(LogLevel.INFO,
                                       f"الجلب السريع محجوب على {host} - "
                                       "تحويل روابطه لمتصفح stealth (scrapling) بدل التأجيل")
                        queue.append((url, depth))
                        continue

                    # Fight before flight #2 - identity rotation: a parked
                    # domain gets ONE identity
                    # rotation per job. Two cases:
                    # 1. proxy modes with an identity pool (tor/hybrid/list/
                    #    rotating): rotate the identity (NEWNYM / next proxy).
                    # 2. Standard connection ("none"): "Tor وقت الحاجة" -
                    #    the direct IP just proved blocked for this domain,
                    #    so NOW (and only now) escalate the run to Tor.
                    #    (_rotate_identity_on_block is fire-and-forget: it
                    #    returns None, so "can we rotate?" is answered by the
                    #    configured mode / Tor reachability.)
                    if self._requeues[host] == 2 and host not in self._rotated_hosts:
                        self._rotated_hosts.add(host)
                        identity_changed = (
                            self.options.proxy.mode in ("tor", "hybrid")
                            or (self.options.proxy.mode in ("list", "rotating")
                                and bool(self._proxy_pool))
                        )
                        if (not identity_changed
                                and self.options.proxy.mode == "none"
                                and anonymity.tor_reachable(self.options.proxy.tor_socks_port)):
                            self.options.proxy.mode = "tor"
                            identity_changed = True
                            self._emit_log(LogLevel.INFO,
                                           f"الاتصال المباشر اتحجب عند {host} - "
                                           "تور اشتغل وقت الحاجة (باقي الكامبين على تور)")
                        if identity_changed:
                            self._rotate_identity_on_block(url, "circuit breaker park")
                            self._brain.unlock(host)
                            self._requeues[host] = 0
                            self._emit_log(LogLevel.INFO,
                                           f"بدّلنا الهوية (IP جديد) - بنعيد محاولة {host} بهوية نضيفة")
                            queue.append((url, depth))
                            continue
                    if self._requeues[host] > 2:
                        dropped_urls = [(u, d) for (u, d) in queue
                                        if brain_mod.host_of(u) == host]
                        queue = deque((u, d) for (u, d) in queue
                                      if brain_mod.host_of(u) != host)
                        dropped = len(dropped_urls)
                        self._emit_log(LogLevel.WARNING,
                                       f"نطاق {host} محجوب (حماية/403) - تخطي {dropped} "
                                       f"رابط متبقٍ منه في هذه المهمة")
                        # checkpoint: the dropped URLs are 'skipped', the
                        # current one too - they exist for a later resume
                        self.db.queue_mark(self.job_id, url, "skipped")
                        for u, _d in dropped_urls:
                            self.db.queue_mark(self.job_id, u, "skipped")
                        if not queue:
                            self._emit_log(LogLevel.WARNING,
                                           "كل الروابط المتبقية من نطاقات محجوبة - "
                                           "إنهاء المهمة مبكرا. شغّل المهمة لاحقا أو "
                                           "فعّل بروكسي/Tor لتغيير الهوية")
                        continue
                    # Genuine park (stealth already tried/failed, or a
                    # browser-lane job): requeue at the END of the queue so
                    # other hosts keep flowing - the old per-deferral 30s
                    # sleep stalled the WHOLE job behind one parked host
                    # (mixed yellowpages/yelp run: yelp's stealth pages
                    # waited out yellowpages' defer cycles for no gain).
                    # Sleep only when nothing else is fetchable, so a
                    # single-host run still waits out the park window
                    # instead of hot-spinning.
                    queue.append((url, depth))
                    self._emit_log(LogLevel.INFO,
                                   f"النطاق تحت ضغط حماية عالي - تأجيل {url} "
                                   f"لآخر القائمة (نافذة ~{int(brain_mod.Brain.BREAKER_PARK_S // 60)} دقيقة)")
                    if queue and all(not self._brain.gate(u) for u, _d in queue):
                        self._interruptible_sleep(30)
                    continue

                # Per-source engine override happens after _resolve_source
                # (below). Robots.txt compliance (audit H5): the flag the
                # UI shows is now actually enforced - disallowed URLs are
                # skipped with a visible log line.
                if self._robots_disallowed(url):
                    pages_done += 1
                    self.db.queue_mark(self.job_id, url, "skipped")
                    self._emit_log(LogLevel.INFO, f"robots.txt يمنع هذا الرابط - تخطي: {url}")
                    emit_progress()
                    continue

                # Pick this request's identity BEFORE fetching: rotate the
                # proxy / Tor circuit so consecutive requests don't leave
                # from the same IP (see _prepare_proxy()).
                self._prepare_proxy(url)
                # Multi-source runs: pick this URL's own container/fields/
                # detail_config before fetching+extracting it - see the
                # source_profiles docstring in __init__ above. A no-op
                # (self.container/self.fields/self.detail_config end up
                # exactly what they already were) when source_profiles
                # isn't set, since _resolve_source() then just returns the
                # _base_* values it was seeded from.
                self.container, self.fields, self.detail_config = self._resolve_source(url)
                self._emit_log(LogLevel.INFO, f"جلب الصفحة: {url}")

                host = brain_mod.host_of(url)
                routed_mode = self._source_fetcher_mode(url)
                effective_mode = routed_mode or self.options.fetcher_mode
                # persistent session for this engine (one per mode per job)
                job_session = self._get_session(effective_mode)
                if host in self._force_browser_hosts and self.options.fetcher_mode == FetcherMode.FAST_HTTP:
                    # This host's fast fetch is a WAF shell - go straight
                    # to the stealth browser (learned earlier this job).
                    fetch_result, error = self._fetch_with_retries(
                        url, options=self._browser_options(),
                        wait_selector=self._container_wait_selector(),
                        session=self._get_session(FetcherMode.STEALTH_BROWSER))
                elif routed_mode is not None and routed_mode != self.options.fetcher_mode:
                    # Per-source engine override: yelp/thumbtack profiles
                    # declare STEALTH (they need a real browser), so
                    # yellowpages pages keep the fast lane while these
                    # still get the browser they need.
                    fetch_result, error = self._fetch_with_retries(
                        url, options=self._options_for_mode(routed_mode),
                        wait_selector=self._container_wait_selector(),
                        session=job_session)
                else:
                    fetch_result, error = self._fetch_with_retries(url, session=job_session)

                if self._stop_requested:
                    break  # Stop was clicked mid-fetch - don't count this as a failed page

                pages_done += 1

                if error is not None:
                    # Fight before flight: a BLOCK (403/429/Cloudflare...) on
                    # the fast lane is the moment to hand the host to the
                    # stealth browser - scrapling's stealth engine is what
                    # passes those walls - instead of finishing the retry
                    # loop, counting the page as failed, and leaving the
                    # rest of the host's URLs to hit the same wall.
                    if (anonymity.looks_like_block(error)
                            and self._stealth_escalation_possible(url, host)):
                        self._stealth_escalated_hosts.add(host)
                        self._force_browser_hosts.add(host)
                        self._requeues[host] = 0
                        if self._brain is not None:
                            self._brain.unlock(host)
                        self._emit_log(LogLevel.WARNING,
                                       f"الحماية رفضت {host} على الجلب السريع - "
                                       "بنحوّل باقي روابطه لمتصفح stealth (scrapling)")
                        # This URL goes back through the queue and comes
                        # back via the browser lane (it was already marked
                        # 'in_progress' and counted as a done page above).
                        self.db.queue_mark(self.job_id, url, "pending")
                        pages_done = max(0, pages_done - 1)
                        queue.append((url, depth))
                        emit_progress()
                        continue
                    records_failed += 1
                    self.db.queue_mark(self.job_id, url, "failed")
                    self._emit_log(LogLevel.ERROR, f"فشل جلب {url}: {error}")
                    self.url_error.emit(url, error)
                    emit_progress()
                    continue

                # WAF rescue: a "successful" fetch whose body is a tiny
                # self-solving challenge shell (AWS WAF - thumbtack.com
                # returns HTTP 202 + a 2KB empty-title shell to plain HTTP)
                # silently produces 0 records. Retry once with the stealth
                # browser, waiting for the source's own container selector;
                # on success the host goes straight-to-browser for the rest
                # of the job, on failure it's marked dead to stop wasting
                # browser runs on it.
                if self._page_is_shell(fetch_result.page) and host not in self._browser_dead_hosts:
                    escalated = None
                    if self.options.fetcher_mode in (FetcherMode.FAST_HTTP, FetcherMode.DYNAMIC_BROWSER):
                        self._emit_log(LogLevel.INFO,
                                       "الصفحة رجعت shell فاضي (تحدي حماية) - "
                                       "إعادة المحاولة بمتصفح stealth...")
                        escalated, _esc_error = self._fetch_with_retries(
                            url, options=self._browser_options(),
                            wait_selector=self._container_wait_selector())
                    if escalated is not None and not self._page_is_shell(escalated.page):
                        fetch_result = escalated
                        self._force_browser_hosts.add(host)
                    else:
                        self._browser_dead_hosts.add(host)

                self._emit_log(LogLevel.SUCCESS, f"تم الجلب، جاري الاستخراج: {url}")
                # Adaptive scraping (official capability): the first page
                # of each host saves the container fingerprint; a zero later
                # gets ONE adaptive relocation retry before we call it empty.
                auto_save = host not in self._adaptive_saved_hosts
                try:
                    records = self._extract(fetch_result.page, auto_save=auto_save)
                except ExtractionError as e:
                    records_failed += 1
                    self._emit_log(LogLevel.ERROR, f"فشل الاستخراج من {url}: {e}")
                    self.progress.emit(pages_done, pages_total, records_ok, records_failed)
                    continue
                if not records and auto_save:
                    try:
                        records = self._extract(fetch_result.page, adaptive=True)
                    except Exception:
                        records = []
                if records and auto_save:
                    self._adaptive_saved_hosts.add(host)

                if not records:
                    # The stealth rescue landed a real page but extracted
                    # nothing from it (e.g. thumbtack's listing grid is
                    # client-gated for bot-tier visitors: 0-1 cards, the
                    # rest of the "10 best" never hydrates). Stop paying
                    # the ~2min stealth cost per URL for this host.
                    if host in self._force_browser_hosts and self.options.fetcher_mode == FetcherMode.FAST_HTTP:
                        self._force_browser_hosts.discard(host)
                        self._browser_dead_hosts.add(host)
                    # Honest signal instead of a green SUCCESS-0 line.
                    if self._page_is_shell(fetch_result.page):
                        self._emit_log(LogLevel.WARNING,
                                       f"0 سجل من {url} - الصفحة رجعت shell فاضي "
                                       f"(تحدي حماية/JS) والموقع محمي ضد الجلب الآلي حاليا")
                    else:
                        self._emit_log(LogLevel.WARNING,
                                       f"0 سجل مطابق للمحددات من {url} - "
                                       f"راجع الـ Container/الحقول لهذا المصدر")
                # Source health (audit §4): distinguish an empty market from
                # a broken selector. A run of consecutive zero-record pages
                # on one host escalates to a specific ERROR, not another
                # quiet zero.
                zeros = self._brain.record_page_outcome(host, bool(records)) if self._brain else (0 if records else 1)
                if records:
                    self.db.queue_mark(self.job_id, url, "done")
                elif zeros >= 10:
                    self.db.queue_mark(self.job_id, url, "done")
                    self._emit_log(LogLevel.ERROR,
                                   f"{host}: {zeros} صفحة متتالية بدون أي سجل - "
                                   "غالباً الـ selectors بتاعة المصدر ده قديمة والمستخرج مش شغال. "
                                   "راجع الـ Container/الحقول أو عطّل المصدر")
                else:
                    self.db.queue_mark(self.job_id, url, "done")

                for record in records:
                    if self.detail_config:
                        self._enrich_with_detail_page(record, url)
                    if self.options.auto_qualify_leads:
                        self._qualify_lead(record, url)
                    if self.options.owner_lookup_enabled:
                        self._lookup_owner_contact_info(record, url)

                    # Cross-job lead history (spec: "هيستوري لليدز اللي طلعت
                    # مسبقا متتكررش كل ما نجينيريت ليدز") - skip a record
                    # that ANY earlier job already produced, so re-running
                    # the same niche/search later surfaces only new leads
                    # instead of re-saving/re-counting old ones. Computed
                    # even when skip_duplicate_leads is off (so it's ready
                    # for later runs where it's on), but only USED to skip
                    # when the option is enabled.
                    fingerprint = fingerprint_lead(record)
                    if self.options.skip_duplicate_leads and fingerprint and self.db.lead_seen_before(fingerprint):
                        duplicates_skipped += 1
                        label = record.get("name") or record.get("company_name") or record.get("email") or url
                        self._emit_log(LogLevel.INFO, f"تم تخطي ليد مكرر (ظهر من قبل): {label}")
                        continue

                    self.db.add_result(self.job_id, url, record)
                    records_ok += 1
                    if fingerprint:
                        self.db.record_lead_seen(fingerprint, self.project_id, self.job_id, record)
                    self.result_ready.emit(record)
                if records:
                    self._emit_log(LogLevel.SUCCESS, f"{len(records)} سجل تم استخراجه من {url}")

                if self.target.follow_links and depth < self.target.max_depth:
                    try:
                        links = engine.extract_links(fetch_result.page, url, self.target.same_domain_only)
                    except Exception as e:
                        links = []
                        self._emit_log(LogLevel.WARNING, f"تعذر استخراج الروابط من {url}: {e}")
                    for link in links:
                        if link not in seen and self._matches_patterns(link):
                            seen.add(link)
                            queue.append((link, depth + 1))
                    if links:
                        self._emit_log(LogLevel.INFO, f"وجدت {len(links)} رابط جديد، أُضيفت لقائمة الانتظار")

                self.db.update_job_progress(self.job_id, pages_done, records_ok, records_failed)
                emit_progress()

            final_status = JobStatus.STOPPED if self._stop_requested else JobStatus.COMPLETED
            self.db.finish_job(self.job_id, final_status.value)
            self.status_changed.emit(final_status.value)
            summary = f"انتهت المهمة: {final_status.value} - {records_ok} سجل ناجح، {records_failed} خطأ"
            if duplicates_skipped:
                summary += f"، {duplicates_skipped} ليد مكرر تم تخطيه (ظهر في سكرابنج سابق)"
            self._emit_log(
                LogLevel.SUCCESS if final_status == JobStatus.COMPLETED else LogLevel.WARNING,
                summary,
            )
        except Exception as e:  # never let one bad page crash the whole run/app
            tb = traceback.format_exc()
            try:
                self.db.finish_job(self.job_id, JobStatus.FAILED.value, error=str(e))
            except Exception:
                pass  # DB write failed too - still fall through to finished.emit() below
            try:
                self.status_changed.emit(JobStatus.FAILED.value)
                self._emit_log(LogLevel.ERROR, f"خطأ غير متوقع أوقف المهمة: {e}\n{tb}")
            except Exception:
                pass
        finally:
            # Persistent sessions (one browser per engine mode) must close
            # before the thread dies, or the browser processes leak.
            try:
                self._close_sessions()
            except Exception:
                pass
            # Always reached, no matter what failed above - this is what
            # lets thread.quit() run (worker.finished -> thread.quit is
            # connected in JobManager.start_job) so the QThread actually
            # stops instead of hanging forever with Stop/pause no longer
            # doing anything.
            self.finished.emit(self.job_id)

    # --- helpers ---
    def _tor_picked(self) -> bool:
        """Is THIS request's identity the Tor endpoint? (Hybrid mode puts
        either a user proxy or Tor into options.proxy.proxies[0].)"""
        proxy = self.options.proxy
        if proxy.mode == "tor":
            return True
        return bool(proxy.mode == "hybrid" and proxy.proxies
                    and proxy.proxies[0].startswith("socks5://127.0.0.1:"))

    def _current_identity_key(self) -> str:
        """Pseudonymous brain key for whatever identity _prepare_proxy()
        just picked (audit C2): raw proxy URLs carry credentials, so the
        brain only ever sees the stable alias - 'px:<hash12>', 'direct'
        or 'tor@<port>' - and the raw string never touches logy.db."""
        proxy = self.options.proxy
        if self._tor_picked():
            return f"tor@{proxy.tor_socks_port}"
        raw = proxy.proxies[0] if proxy.proxies else "direct"
        return self._alias_of(raw)

    def _build_hybrid_pool(self) -> list[str]:
        """One rotation pool out of the user's proxies + the Tor endpoint.
        This is what lets a site that blocks Tor exit IPs be scraped
        anyway: identities alternate, and blocked ones get skipped (see
        _rotate_identity_on_block)."""
        proxy = self.options.proxy
        pool = [p for p in self._proxy_pool if p.strip()]
        tor_url = anonymity.tor_socks_url(proxy.tor_socks_port, for_http=False)
        if tor_url not in pool:
            pool.append(tor_url)
        return pool

    def _identity_pool(self) -> list[str]:
        """The active identity pool for the current proxy mode (raw strings)."""
        proxy = self.options.proxy
        if proxy.mode in ("list", "rotating"):
            return [p for p in self._proxy_pool if p.strip()]
        if proxy.mode == "hybrid":
            return self._build_hybrid_pool()
        if proxy.mode == "tor":
            return [f"tor@{proxy.tor_socks_port}"]
        return []

    def _alias_of(self, raw_key: str) -> str:
        """Pseudonymous identity key for persistence (audit C2): raw proxy
        URLs contain credentials and must never reach identity_stats.
        'direct' and local Tor endpoint keys carry no secrets and stay
        readable; everything else becomes a stable short hash."""
        if raw_key == "direct" or raw_key.startswith("tor@"):
            return raw_key
        if raw_key.startswith("socks5://127.0.0.1:"):
            return raw_key  # hybrid pool's local Tor endpoint - no secret
        alias = "px:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:12]
        self._raw_by_alias.setdefault(alias, raw_key)
        return alias

    def _raw_of(self, alias: str) -> str:
        """Reverse map back to the raw proxy string for the adapter."""
        return self._raw_by_alias.get(alias, alias)

    def _alias_pool(self, pool: list[str]) -> list[str]:
        out = []
        for raw in pool:
            alias = self._alias_of(raw)
            out.append(alias)
        return out

    def _setup_brain(self):
        """Create the Brain lazily: only when at least one intelligence
        feature can actually use it. The Brain LOADS everything previous
        jobs learned about these identities/domains from logy.db - that
        inheritance is the whole point of cross-run memory."""
        opts = self.options
        if not (opts.use_identity_memory or opts.use_response_cache
                or opts.pacing_mode in ("aimd", "burst")):
            return
        try:
            self._brain = brain_mod.Brain(self.db)
        except Exception as e:
            self._emit_log(LogLevel.WARNING, f"تعذر تهيئة ذاكرة التعلم - الاستمرار بدونها: {e}")
            return
        if opts.use_identity_memory and self._identity_pool():
            self._emit_log(LogLevel.INFO,
                           f"ذاكرة الهويات مفعّلة: {len(self._identity_pool())} هوية في الدورة "
                           "(سمعة كل هوية محفوظة عبر الجريات)")
        if opts.pacing_mode == "burst":
            self._pacer = brain_mod.BurstPacer()
            self._emit_log(LogLevel.INFO, "وضع النبض البشري (burst) مفعّل: إيقاع الطلبات بيتولّد من عملية ذاتية الاستثارة")
        elif opts.pacing_mode == "aimd":
            self._emit_log(LogLevel.INFO, "الوضع التكيفي (AIMD) مفعّل: السرعة بتتظبط لكل نطاق تلقائياً")

    def _probe_proxies_at_start(self):
        """Pre-flight: one cheap parallel request per proxy BEFORE the job,
        so dead entries leave the pool at t=0 instead of surfacing mid-run
        as mystery failures that pollute the identity statistics.

        Free-feed refresh (proxifly via curl) runs here too, so "rotating"
        and "hybrid" campaigns start from a FRESH list every run - the
        community list churns hourly, so an old snapshot rots fast."""
        proxy = self.options.proxy
        # Re-download the community list on every campaign that uses
        # proxies (the user's requirement: "يجدد البروكسيز كل مرة").
        if proxy.mode in ("list", "rotating", "hybrid"):
            ok, count, msg = proxy_feed.refresh_free_proxies(force=True)
            self._emit_log(LogLevel.INFO if ok else LogLevel.WARNING, f"تحديث القائمة المجانية: {msg}")
            if ok and not self._proxy_pool:
                fresh = proxy_feed.load_list()
                if fresh:
                    self._proxy_pool = fresh
                    self._rotator = None
                    self._emit_log(LogLevel.INFO,
                                   f"تم تحميل {len(fresh)} بروكسي من القائمة المجانية (proxifly) - بتتدور تلقائياً")

        candidates = [p for p in self._proxy_pool if p.strip()] if proxy.mode in ("list", "rotating", "hybrid") else []
        if not candidates:
            return
        self._emit_log(LogLevel.INFO, f"فحص صحة {len(candidates)} بروكسي قبل البدء...")
        alive, dead = engine.probe_proxies(candidates)
        if dead:
            self._proxy_pool = alive
            if self._rotator is not None:
                self._rotator = None    # force rebuild from the pruned pool
            self._emit_log(LogLevel.WARNING,
                           f"{len(dead)} بروكسي ميت اتشال من الدورة قبل البدء - الباقي {len(alive)}")
        else:
            self._emit_log(LogLevel.SUCCESS, f"كل البروكسيات ({len(alive)}) شغالة")

    def _expand_sitemap(self):
        """Sitemap-first discovery: pull crawl targets from the target's own
        sitemap.xml (static, unprotected, CDN-served) instead of fighting
        for them on protected search pages. Runs before the main loop and
        MERGES results into start_urls (originals kept as fallback)."""
        if not self.options.discover_sitemap or not self.target.start_urls:
            return
        self._emit_log(LogLevel.INFO, "جاري اكتشاف الروابط من sitemap.xml...")

        def _fetch_body(u: str) -> Optional[str]:
            try:
                opts = ScrapeOptions(fetcher_mode=FetcherMode.FAST_HTTP, timeout_s=15, retries=0)
                fr = engine.fetch_one(u, opts, should_stop=lambda: self._stop_requested)
                return engine.get_html(fr.page)
            except Exception:
                return None

        from app.core.engine import sitemap as sitemap_mod
        try:
            found = sitemap_mod.discover_sitemap_urls(
                self.target.start_urls, _fetch_body,
                include_patterns=self.target.include_patterns,
                exclude_patterns=self.target.exclude_patterns,
                max_urls=max(self.target.max_pages, 200))
        except Exception as e:
            self._emit_log(LogLevel.WARNING, f"تعذر قراءة الـ sitemap: {e}")
            return
        if found:
            merged = list(dict.fromkeys(self.target.start_urls + found))
            self.target.start_urls = merged
            self._emit_log(LogLevel.SUCCESS,
                           f"sitemap أضاف {len(found)} رابط من صفحات الأعمال - إجمالي الطابور {len(merged)}")
        else:
            self._emit_log(LogLevel.INFO, "مفيش روابط إضافية من الـ sitemap - الطابور زي ما هو")

    def _pacing_sleep(self, url: str) -> bool:
        """True when the intelligence layer owned this sleep (brain modes),
        False → caller falls back to the flat options.delay_ms sleep.
        Effective wait = the per-domain AIMD delay, optionally wrapped in
        the burst pacer's self-exciting gap (with AIMD as the floor, so a
        domain that just pushed back stays slowed regardless of bursts).
        The pacer's own distribution is capped relative to the user's
        delay_ms: its quiet-regime mean is ~20s by design, which silently
        turned a 2000ms user delay into a ~30s-per-request crawl (found
        by timing instrumented runs - pacing dwarfed actual fetching)."""
        if self._brain is None:
            return False
        host = brain_mod.host_of(url)
        aimd_s = self._brain.delay_s(host)
        if self._pacer is not None:
            gap = self._pacer.sample(floor_s=aimd_s)
            user_s = self.options.delay_ms / 1000.0
            cap = max(user_s * 3.0, aimd_s) + 1.0
            self._interruptible_sleep(min(gap, cap))
        elif aimd_s > 0:
            self._interruptible_sleep(aimd_s)
        else:
            return False
        return True

    def _stealth_escalation_possible(self, url: str, host: str) -> bool:
        """True when a blocked host can still be fought with the stealth
        browser: fast-HTTP job (the lane the _force_browser_hosts branch
        routes from), the host's source profile doesn't already declare a
        browser lane (yelp/thumbtack), and stealth hasn't already been
        tried and failed for it this job."""
        if self.options.fetcher_mode != FetcherMode.FAST_HTTP:
            return False
        if host in self._stealth_escalated_hosts or host in self._browser_dead_hosts:
            return False
        return self._source_fetcher_mode(url) is None

    def _source_fetcher_mode(self, url: str):
        """The engine this URL's source profile declares (STEALTH for
        yelp/thumbtack, which need a real browser; None = inherit the
        job's fetcher_mode, i.e. FAST_HTTP for yellowpages). This keeps
        the WAF-heavy sources on the browser WITHOUT paying the ~5-27s
        stealth cost on every yellowpages page that doesn't need it."""
        if not self.source_profiles:
            return None
        host = urlparse(url).netloc.lower()
        for profile in self.source_profiles:
            if profile.get("domain") and profile["domain"] in host:
                return profile.get("fetcher_mode")
        return None

    def _options_for_mode(self, mode: FetcherMode) -> ScrapeOptions:
        """Options copy for a per-source engine override (cached per mode).
        Browser modes never touch the FAST_HTTP response cache."""
        cached = self._mode_options.get(mode)
        if cached is None:
            from dataclasses import replace as _dc_replace
            cached = _dc_replace(
                self.options,
                fetcher_mode=mode,
                use_response_cache=(mode == FetcherMode.FAST_HTTP),
            )
            self._mode_options[mode] = cached
        return cached

    def _record_outcome(self, url: str, blocked: bool, latency_s: float = 0.0, error: str = ""):
        """Feed the fetch result back into the Brain: Beta posterior update,
        cooldown on block, AIMD step, WAF log-odds, breaker check. When the
        experimental PoW option is on and the block reason smells like a JS
        challenge, say so in the log (full native handshake is staged in
        app/core/engine/pow_solver.py and needs a live target to tune)."""
        if self._brain is None:
            return
        host = brain_mod.host_of(url)
        key = self._current_identity_key()
        self._brain.record(key, host, blocked, latency_s)
        if blocked and self.options.solve_pow and "challenge" in error.lower():
            self._emit_log(LogLevel.INFO,
                           f"الحظر على {url} شكله تحدي JS proof-of-work - "
                           "المحلل التجريبي موجود في pow_solver.py ومحتاج ضبط على هدف حقيقي")

    def _rotate_identity_on_block(self, url: str, reason: str) -> None:
        """Called when a fetch comes back blocked (403/429/Cloudflare...).
        Retry-on-block is where 'sites that block Tor' get beaten: instead
        of waiting tor_rotate_every requests, drop this identity NOW -
        Tor gets an immediate NEWNYM (new exit node), proxy lists advance
        to the next entry - so the retry goes out from a DIFFERENT IP."""
        proxy = self.options.proxy
        if proxy.mode == "tor":
            ok, msg = anonymity.rotate_tor_circuit(proxy.tor_control_port, proxy.tor_control_password)
            self._emit_log(LogLevel.WARNING if not ok else LogLevel.INFO,
                           f"الموقع رفض الهوية ({url}) - تدوير فوري لدائرة Tor: {msg}")
            return
        if proxy.mode == "hybrid":
            if self._tor_picked():
                ok, msg = anonymity.rotate_tor_circuit(proxy.tor_control_port, proxy.tor_control_password)
                level = LogLevel.INFO if ok else LogLevel.DEBUG
                self._emit_log(level, f"بلوك على هوية Tor من {url}: {msg}")
            if self._rotator is not None and len(self._rotator) > 1:
                # advance past the blocked identity (and one more when the
                # pool is big enough) - the LAST advanced-to entry IS the
                # new identity, pinned for the retry that's about to run
                # inside _fetch_with_retries (before the next
                # _prepare_proxy() would). The re-advance guard below keeps
                # the pinned identity != the blocked one even if the
                # rotator's internal index drifted out of sync with
                # proxy.proxies (tiny pools, manual state changes).
                old = proxy.proxies[0] if proxy.proxies else None
                skip = min(2, len(self._rotator) - 1)
                advanced = [self._rotator.next() for _ in range(skip)]
                new = advanced[-1]
                if new == old and len(self._rotator) > 1:
                    new = self._rotator.next()
                proxy.proxies = [new]
                self._emit_log(LogLevel.INFO,
                               f"بلوك ({url}) - تجاوز {', '.join(advanced[:-1]) or 'الهوية الحالية'} "
                               f"والتحويل للهوية: {new}")
        elif proxy.mode in ("list", "rotating"):
            if self._rotator is None:
                self._rotator = anonymity.CyclicProxyRotator(self._proxy_pool or [p for p in proxy.proxies if p])
            if len(self._rotator) > 1:
                old = proxy.proxies[0] if proxy.proxies else None
                new = self._rotator.next()
                if new == old and len(self._rotator) > 1:
                    new = self._rotator.next()
                proxy.proxies = [new]
                self._emit_log(LogLevel.INFO, f"بلوك ({url}) - تبديل البروكسي إلى: {new}")

    def _prepare_proxy(self, url: str = "") -> None:
        """Runs before EVERY fetch: makes options.proxy.proxies exactly one
        entry - the one this particular request should use.

        - "list"/"rotating": cyclically rotate through the user's list.
          This is the actual fix for the reported behavior 'الموقع بيكشف
          إن فيه ريكويستات كتير': the UI collected a proxy list but the
          adapter always used proxies[0], so every request went out from
          the SAME IP no matter which mode was picked.
        - "tor": always the local Tor SOCKS endpoint, and every
          tor_rotate_every requests we signal Tor (NEWNYM) to rebuild its
          circuit so the exit IP changes mid-run.
        - "hybrid": one cyclic pool = the user's proxies + the Tor
          endpoint (the anti-"sites that block Tor" mode), with the same
          periodic NEWNYM rotation whenever Tor is the picked identity.
        - "none"/"single": unchanged (single entry or empty already).

        With identity memory active (brain), list/hybrid picking upgrades
        from blind cyclic to the Brain's weighted, cooldown-aware,
        sticky-per-domain choice (see brain.Brain.pick) and the request's
        persona (UA/locale/timezone) is pinned to the chosen identity.

        Mutating self.options (instead of threading a separate proxy arg
        through fetch_one) is deliberate: options.proxy is already the
        single source the adapter and every enrichment path (qualifier,
        detail pages, owner lookup) read, so one write here covers all of
        them."""
        proxy = self.options.proxy
        if proxy.mode in ("list", "rotating") and len(self._proxy_pool) > 1:
            if self._brain is not None and self.options.use_identity_memory:
                # Brain sees only the pseudonymous aliases (C2); the chosen
                # alias maps back to the raw proxy for the adapter.
                pool = self._alias_pool(self._identity_pool())
                picked = self._brain.pick(pool, brain_mod.host_of(url), self.options.identity_selection)
                proxy.proxies = [self._raw_of(picked)]
            else:
                if self._rotator is None:
                    self._rotator = anonymity.CyclicProxyRotator(self._proxy_pool)
                proxy.proxies = [self._rotator.next()]
            self._sync_persona()
            return
        if proxy.mode in ("tor", "hybrid"):
            if proxy.mode == "hybrid":
                pool = self._build_hybrid_pool()
                if len(pool) > 1:
                    if self._brain is not None and self.options.use_identity_memory:
                        aliases = self._alias_pool(pool)
                        picked = self._brain.pick(aliases, brain_mod.host_of(url), self.options.identity_selection)
                        proxy.proxies = [self._raw_of(picked)]
                    else:
                        if self._rotator is None or len(self._rotator) != len(pool):
                            self._rotator = anonymity.CyclicProxyRotator(pool)
                        proxy.proxies = [self._rotator.next()]
                else:
                    proxy.proxies = [pool[0]]
            else:
                proxy.proxies = [anonymity.tor_socks_url(proxy.tor_socks_port, for_http=False)]
            self._request_seq += 1
            every = max(0, proxy.tor_rotate_every)
            if every and self._tor_picked() and self._request_seq % every == 0:
                ok, reason = anonymity.rotate_tor_circuit(proxy.tor_control_port, proxy.tor_control_password)
                level = LogLevel.SUCCESS if ok else LogLevel.WARNING
                self._emit_log(level, f"تدوير هوية Tor ({self._request_seq}): {reason}")
            self._sync_persona()

    def _sync_persona(self):
        """Pin the picked identity's persona onto options so the adapter
        ships this request as the SAME 'person' every time this identity
        is used - IP + fingerprint stay a consistent pair."""
        if self._brain is None or not self.options.use_identity_memory:
            self.options.persona = {}
            return
        self.options.persona = self._brain.identity(self._current_identity_key()).persona

    def _check_tor_at_start(self) -> None:
        """One-time startup sanity check for tor mode: without a listening
        SOCKS port every single fetch would fail with the same confusing
        connection error - say so ONCE, clearly, instead."""
        proxy = self.options.proxy
        if proxy.mode not in ("tor", "hybrid"):
            return
        if proxy.mode == "tor":
            if anonymity.tor_reachable(proxy.tor_socks_port):
                self._emit_log(LogLevel.SUCCESS,
                               f"وضع مجهول مفعّل: الترافيك هيعدّي على Tor (127.0.0.1:{proxy.tor_socks_port})"
                               + (f"، تدوير الكيركيت كل {proxy.tor_rotate_every} ريكويست" if proxy.tor_rotate_every else ""))
            else:
                self._emit_log(LogLevel.ERROR,
                               f"مفيش حاجة شغالة على 127.0.0.1:{proxy.tor_socks_port} - شغّل Tor الأول "
                               "(Tor Browser أو tor.exe) وإلا كل الريكويستات هتفشل")
        else:  # hybrid: Tor is one identity among the user's proxies
            if not self._build_hybrid_pool() or not anonymity.tor_reachable(proxy.tor_socks_port):
                self._emit_log(LogLevel.WARNING,
                               f"وضع Hybrid: مفيش حاجة شغالة على منفذ Tor {proxy.tor_socks_port} - "
                               "التدوير هيكمل على البروكسيات بس. شغّل Tor لو عايز التور يشارك.")
            elif len(self._build_hybrid_pool()) < 2:
                self._emit_log(LogLevel.WARNING,
                               "وضع Hybrid محتاج بروكسي واحد على الأقل في القايمة غير التور - "
                               "حالياً كل الريكويستات هتعدّي على Tor بس")

    def _page_is_shell(self, page) -> bool:
        """True when a fetched page is almost certainly a bot-challenge
        shell rather than real content: an empty <title> (the AWS WAF
        challenge shell has <title></title>) or a body so small no real
        listing page would fit it. Real yellowpages/yelp search pages are
        100KB+; the thumbtack challenge shell is ~2KB with no title."""
        try:
            html = engine.get_html(page) or ""
        except Exception:
            return False
        if not html:
            return True
        return "<title></title>" in html or len(html) < 2500

    def _robots_disallowed(self, url: str) -> bool:
        """robots.txt compliance (audit H5 - the flag existed in the UI and
        TargetConfig but was never enforced). One fetch per host per job,
        cached; unreachable/unparseable robots.txt fails OPEN (a dead
        robots endpoint must not silently disable a whole source)."""
        if not self.target.respect_robots_txt:
            return False
        host = brain_mod.host_of(url)
        if host not in self._robots_cache:
            parser = None
            try:
                parts = urlparse(url)
                origin = f"{parts.scheme}://{parts.netloc}"
                robots_opts = ScrapeOptions(fetcher_mode=FetcherMode.FAST_HTTP,
                                            timeout_s=8, retries=0)
                fr = engine.fetch_one(origin + "/robots.txt", robots_opts,
                                      should_stop=lambda: self._stop_requested)
                rp = robotparser.RobotFileParser()
                rp.parse((engine.get_html(fr.page) or "").splitlines())
                parser = rp
            except Exception as e:
                self._emit_log(LogLevel.DEBUG, f"تعذر قراءة robots.txt لـ {host} - متابعة (fail-open): {e}")
            self._robots_cache[host] = parser
        parser = self._robots_cache[host]
        if parser is None:
            return False
        try:
            return not parser.can_fetch("*", url)
        except Exception:
            return False

    def _container_wait_selector(self) -> Optional[str]:
        """The resolved source's container selector, used as the browser's
        wait target so the snapshot lands AFTER challenge-solve + hydration."""
        return (self.container or {}).get("selector") or None

    def _get_session(self, mode: FetcherMode):
        """Open (once per mode per job) the persistent session handle for
        this engine. Failures fall back to per-page fetches with one debug
        line - a session problem must never kill a run."""
        if not self.options.use_sessions:
            return None
        if mode not in self._sessions:
            try:
                handle = engine.open_session(self.options, mode)
                if handle is not None:
                    handle.enter()
                self._sessions[mode] = handle
            except Exception as e:
                self._emit_log(LogLevel.DEBUG,
                               f"تعذر فتح جلسة {mode.value} - الجلب هيتم لكل صفحة على حدة: {e}")
                self._sessions[mode] = None
        return self._sessions[mode]

    def _close_sessions(self):
        for mode, handle in list(self._sessions.items()):
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass
            self._sessions[mode] = None

    def _browser_options(self):
        """Options copy for the WAF-rescue / straight-to-browser fetch:
        stealth engine, no response cache. The generous timeout covers the
        full challenge-solve → reload → redirect → render sequence (AWS WAF
        ~10s + thumbtack's category redirect ate a 90s budget once)."""
        from dataclasses import replace as _dc_replace
        return _dc_replace(
            self.options,
            fetcher_mode=FetcherMode.STEALTH_BROWSER,
            use_response_cache=False,
            retries=0,
            timeout_s=max(self.options.timeout_s, 150),
        )

    def _fetch_with_retries(self, url: str, options: Optional[ScrapeOptions] = None,
                            wait_selector: Optional[str] = None,
                            session=None) -> tuple:
        options = options or self.options
        last_error = None
        attempts = max(1, options.retries + 1)
        for attempt in range(1, attempts + 1):
            if self._stop_requested:
                return None, "أوقفه المستخدم"
            started = time.time()
            try:
                # should_stop lets a hung/slow fetch be interrupted within
                # ~0.25s of a Stop click, instead of only being checked
                # between whole retry attempts (which could be up to
                # timeout+15s apart) - that gap was why Stop looked broken.
                result = engine.fetch_one(url, options,
                                          should_stop=lambda: self._stop_requested,
                                          cache=self._brain,
                                          wait_selector=wait_selector,
                                          session=session)
                if self._brain is not None:
                    # Learning step: success feeds the Beta posterior, AIMD
                    # speed-up and WAF-state decay for this domain+identity.
                    self._record_outcome(url, blocked=False, latency_s=time.time() - started)
                return result, None
            except engine.FetchCancelled:
                return None, "أوقفه المستخدم"
            except engine.FetchError as e:
                last_error = str(e.reason)
                if attempt < attempts:
                    # A block (403/429/Cloudflare...) is a statement about
                    # the IDENTITY, not the URL - retrying from the same IP
                    # just gets blocked again (this was Yelp's whole 403
                    # wall). Rotate the identity first, THEN retry.
                    if anonymity.looks_like_block(last_error):
                        if self._brain is not None:
                            self._record_outcome(url, blocked=True,
                                                 latency_s=time.time() - started, error=last_error)
                        self._rotate_identity_on_block(url, last_error)
                    self._emit_log(LogLevel.WARNING, f"إعادة محاولة {attempt}/{attempts - 1} لـ {url}: {last_error}")
                    self._interruptible_sleep(min(2 ** attempt, 10))
                    if self._stop_requested:
                        return None, "أوقفه المستخدم"
                elif self._brain is not None and anonymity.looks_like_block(last_error):
                    # final failed attempt still counts as a block signal
                    self._record_outcome(url, blocked=True,
                                         latency_s=time.time() - started, error=last_error)
            except RuntimeError as e:  # the fetch engine not installed
                return None, str(e)
        return None, last_error

    def _interruptible_sleep(self, seconds: float):
        """time.sleep() that bails early if Stop is clicked, so the
        exponential backoff between retries can't itself block Stop."""
        end = time.time() + seconds
        while time.time() < end and not self._stop_requested:
            time.sleep(min(0.2, max(0.0, end - time.time())))

    def _ai_budget_available(self) -> bool:
        """Hard per-run ceiling on paid AI calls (audit H6): owner-lookup
        and auto-extract share one budget so a 2,000-lead run can't turn
        into a surprise bill."""
        budget = self.options.ai_extraction.ai_call_budget
        if not budget:
            return True
        if self._ai_calls >= budget:
            if not self._ai_budget_warned:
                self._ai_budget_warned = True
                self._emit_log(LogLevel.WARNING,
                               f"تم الوصول لحد ميزانية الذكاء الاصطناعي ({budget} نداء) - "
                               "باقي التشغيل هيتكم بدون نداءات AI مدفوعة")
            return False
        return True

    def _mark_ai_fields(self, record: dict, keys: list[str]) -> None:
        """Provenance marker (audit H6): AI-inferred values are stored
        alongside the record as an explicit list, so an exported lead can
        always be separated into SELECTOR-EXTRACTED FACT vs AI INFERENCE.
        The fields themselves stay in place for usability - the marker is
        what keeps the distinction honest."""
        if keys:
            merged = set(record.get("_ai_extracted_fields") or [])
            merged.update(keys)
            record["_ai_extracted_fields"] = sorted(merged)

    def _qualify_lead(self, record: dict, source_url: str) -> None:
        """Automatic 'weak digital marketing' check (spec: ICP criterion
        #5). If this record has a website field, fetch that site (fast
        HTTP, short timeout - this is a quick homepage check, not a full
        crawl) and score it with qualifier.qualify_html(). Adds
        digital_score / digital_label / digital_signals to the record
        in place. Never lets a failed fetch break the main scrape - a
        lead with an unreachable site is itself a strong "weak digital
        presence" signal, not an error."""
        from urllib.parse import urljoin
        from app.core.models import ScrapeOptions as _Opts, FetcherMode as _FM

        website = record.get("website")
        if not website or not isinstance(website, str):
            result = qualify_html(None)
            record["digital_score"] = result.score
            record["digital_label"] = result.label
            record["digital_signals"] = "; ".join(result.signals)
            return

        website_url = urljoin(source_url, website)
        # The breaker guards enrichment fetches too (audit §6): a parked
        # host must not be hammered once per lead by the quality checks.
        if self._brain is not None and not self._brain.gate(website_url):
            self._emit_log(LogLevel.DEBUG, f"النطاق تحت ضغط - تخطي فحص الجودة: {website_url}")
            return
        quick_check_options = _Opts(fetcher_mode=_FM.FAST_HTTP, timeout_s=10, retries=0)
        try:
            # Direct call, not through _fetch_with_retries - a dead lead
            # site shouldn't retry/backoff and slow down the whole job.
            fr = engine.fetch_one(website_url, quick_check_options, should_stop=lambda: self._stop_requested)
            html = engine.get_html(fr.page)
            result = qualify_html(html)
        except Exception as e:
            self._emit_log(LogLevel.DEBUG, f"تعذر فحص موقع {website_url}: {e}")
            result = qualify_html(None)
            result.signals = [f"تعذّر الوصول للموقع: {e}"]

        record["digital_score"] = result.score
        record["digital_label"] = result.label
        record["digital_signals"] = "; ".join(result.signals)

    def _lookup_owner_contact_info(self, record: dict, source_url: str) -> None:
        """'اسم البيزنيس + الميل بتاع الاونر + اللينكد ان بروفايل بتاع
        الاونر + رقم تليفون الاونر' - Yelp/yellowpages' OWN listing pages
        never publish owner-level personal contact info (Yelp exposes the
        BUSINESS's phone at most - see _enrich_with_detail_page() above -
        never a named owner's personal email/phone/LinkedIn), so no
        selector or regex against those pages can produce fields that
        simply aren't there on them. The one place that kind of info is
        sometimes legitimately public is the lead's OWN business website
        (an "About Us" / "Meet the Owner" / "Contact" page the business
        chose to publish itself) - reading THAT is "read a business's own
        published info", not the automated LinkedIn people-search this
        project has repeatedly and deliberately declined to build (see
        app/core/engine/ai_extractor.py's module docstring - still
        applies here unchanged). Best-effort and silent on any failure -
        never fabricates an owner_* field; a lead simply keeps whatever
        it already had if this can't find anything."""
        website = record.get("website")
        if not website or not isinstance(website, str):
            return

        provider = self.options.ai_extraction.provider
        api_key = self._resolve_api_key(provider)
        if not api_key:
            self._emit_log(LogLevel.WARNING, f"لا يوجد مفتاح API محفوظ لـ '{provider}' - تخطي البحث عن بيانات المالك")
            return

        from urllib.parse import urljoin

        website_url = urljoin(source_url, website)
        owner_fields = ["owner_name", "owner_email", "owner_phone", "owner_linkedin_if_published"]
        if not self._ai_budget_available():
            return
        try:
            fr = engine.fetch_one(website_url, self.options, should_stop=lambda: self._stop_requested)
            html = engine.get_html(fr.page)
            text = engine.html_to_text(html)
            owner_data = ai_extractor.extract(provider, text, owner_fields, api_key)
            self._ai_calls += 1
        except Exception as e:
            self._emit_log(LogLevel.DEBUG, f"تعذر البحث عن بيانات مالك من {website_url}: {e}")
            return

        filled = []
        for key, value in owner_data.items():
            if value not in (None, ""):
                record[key] = value
                filled.append(key)
        if filled:
            self._mark_ai_fields(record, filled)

    def _enrich_with_detail_page(self, record: dict, source_url: str) -> None:
        """Fill in fields that only exist on a per-business detail page,
        not on the listing/search-results page the record was extracted
        from - e.g. yelp.com's search results give a name and a link to
        the business's own page, but no phone number at all; the phone
        only shows up on that linked page. self.detail_config (set from
        a template's config, see app/core/engine/builtin_templates.py's
        _YELP_DETAIL_CONFIG) says which record field holds that link and
        what to pull off the fetched page.

        Mirrors _qualify_lead() above: one extra fetch per record, best-
        effort, never lets a failed detail fetch break the main scrape -
        a record that couldn't be enriched just keeps whatever fields it
        already had from the listing page.
        """
        from urllib.parse import urljoin

        link_field = self.detail_config.get("link_field")
        detail_fields = self.detail_config.get("fields") or []
        regex_fields = self.detail_config.get("regex_fields") or {}
        if not link_field or (not detail_fields and not regex_fields):
            return

        link = record.get(link_field)
        if not link or not isinstance(link, str):
            return

        detail_url = urljoin(source_url, link)
        # Breaker guard (audit §6): don't hammer a parked host per-lead.
        if self._brain is not None and not self._brain.gate(detail_url):
            self._emit_log(LogLevel.DEBUG, f"النطاق تحت ضغط - تخطي صفحة التفاصيل: {detail_url}")
            return
        try:
            fr = engine.fetch_one(detail_url, self.options, should_stop=lambda: self._stop_requested)
        except Exception as e:
            self._emit_log(LogLevel.DEBUG, f"تعذر جلب تفاصيل من {detail_url}: {e}")
            return

        if detail_fields:
            try:
                extra = extract_fields(fr.page, detail_fields)
                for k, v in extra.items():
                    if v not in (None, ""):
                        record[k] = v
            except ExtractionError as e:
                self._emit_log(LogLevel.DEBUG, f"فشل استخراج تفاصيل من {detail_url}: {e}")

        if regex_fields:
            html = engine.get_html(fr.page)
            text = engine.html_to_text(html)
            for field_name, pattern in regex_fields.items():
                if record.get(field_name):  # don't clobber a value the listing page already gave us
                    continue
                m = re.search(pattern, text)
                if m:
                    record[field_name] = m.group(0)

    def _resolve_source(self, url: str) -> tuple[Optional[dict], list[ExtractionField], Optional[dict]]:
        """Return the (container, fields, detail_config) to use for this
        specific URL. With no source_profiles set, always returns the
        worker's own single set (_base_container/_base_fields/
        _base_detail_config) unchanged - identical to the pre-multi-source
        behavior. With source_profiles set (a combined run mixing more
        than one site's URLs in one job), matches the URL's domain against
        each profile's "domain" and returns that profile's selectors, so
        e.g. a yelp.com URL gets Yelp's container/fields/detail_config
        even though a yellowpages.com URL earlier in the same queue got
        yellowpages.com's. A URL whose domain matches no profile (shouldn't
        normally happen - see the callers that build source_profiles) logs
        a warning and falls back to the worker's own single set rather
        than silently extracting nothing."""
        if not self.source_profiles:
            return self._base_container, self._base_fields, self._base_detail_config
        host = urlparse(url).netloc.lower()
        for profile in self.source_profiles:
            if profile.get("domain") and profile["domain"] in host:
                return profile.get("container"), profile.get("fields") or self._base_fields, profile.get("detail_config")
        self._emit_log(LogLevel.WARNING, f"لا يوجد إعداد استخراج معروف لمصدر هذا الرابط: {url} - هيتستخدم الإعداد الافتراضي")
        return self._base_container, self._base_fields, self._base_detail_config

    def _extract(self, page, *, adaptive: bool = False, auto_save: bool = False) -> list[dict]:
        if self.options.ai_extraction.enabled:
            return self._extract_with_ai(page)
        if self.container and self.container.get("selector"):
            return extract_records(page, self.container["selector"], self.container.get("type", "css"),
                                   self.fields, adaptive=adaptive, auto_save=auto_save)
        return [extract_fields(page, self.fields)]

    def _extract_with_ai(self, page) -> list[dict]:
        """No-selector extraction path: read the whole page as text and
        let the LLM fill in the requested field names. One record per
        page (AI Auto-Extract is aimed at "one business per page" targets
        like a company's own site or a directory profile page, not
        listing pages with many cards - use Custom Selector for those)."""
        ai_cfg = self.options.ai_extraction
        api_key = self._resolve_api_key(ai_cfg.provider)
        if not self._ai_budget_available():
            raise ExtractionError("ai_budget", "تم استهلاك ميزانية نداءات الذكاء الاصطناعي لهذه المهمة")
        html = engine.get_html(page)
        text = engine.html_to_text(html)
        try:
            record = ai_extractor.extract(ai_cfg.provider, text, ai_cfg.field_names, api_key)
            self._ai_calls += 1
        except ai_extractor.AIExtractionError as e:
            raise ExtractionError("ai_extract", str(e)) from e
        self._mark_ai_fields(record, [f for f in ai_cfg.field_names if record.get(f) not in (None, "")])
        return [record]

    def _resolve_api_key(self, provider: str) -> str:
        keys = self.db.get_setting("api_keys", {})
        encrypted = keys.get(provider)
        if not encrypted:
            return ""
        try:
            return SecretStore().decrypt(encrypted)
        except Exception:
            return ""

    def _matches_patterns(self, url: str) -> bool:
        import fnmatch
        if self.target.exclude_patterns and any(fnmatch.fnmatch(url, p) for p in self.target.exclude_patterns):
            return False
        if self.target.include_patterns:
            return any(fnmatch.fnmatch(url, p) for p in self.target.include_patterns)
        return True

    def _emit_log(self, level: LogLevel, message: str):
        # Central redaction sink (audit C2): every job log line passes
        # through here, so scheme://user:pass@host can never reach
        # logy.db or the UI feed even if a caller forgets.
        safe = redact_secrets(message)
        self.db.add_log(self.job_id, level.value, safe)
        self.log.emit(level.value, safe)


class JobManager(QObject):
    """Owns the worker + thread lifecycle so screens never touch QThread directly."""

    def __init__(self, db: Database):
        super().__init__()
        self.db = db
        self._thread: Optional[QThread] = None
        self._worker: Optional[ScrapeJobWorker] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def prepare_job(self, project_id: Optional[int], target: TargetConfig, fields: list[ExtractionField],
                     options: ScrapeOptions, container: Optional[dict] = None,
                     detail_config: Optional[dict] = None,
                     source_profiles: Optional[list[dict]] = None) -> tuple[int, ScrapeJobWorker]:
        """Build the worker + QThread and wire the internal plumbing
        (finished -> thread.quit, cleanup, ...), but do NOT start the
        thread yet.

        This used to be one method (start_job) that started the thread
        immediately and returned the worker, leaving the caller to
        connect its own UI slots (log/progress/result_ready/...)
        afterward. That was a race: thread.start() can let the new
        thread's run() begin - and it immediately emits a status/log
        signal - before the caller's next lines of Python even execute
        the .connect() calls back on the GUI thread. Qt does not queue or
        replay a signal emitted before a connection existed; it's just
        dropped. Depending on scheduling, that could mean the log panel
        and progress bar silently miss the run's very first (or, if the
        OS scheduler was unlucky, several) updates - exactly what was
        reported as "progress bar / log not updating".

        Call this first, connect every UI signal to the returned worker,
        THEN call start_prepared_job() - that ordering guarantees no
        emission can happen before something is listening.
        """
        if self.is_running:
            raise RuntimeError("مهمة تانية شغالة بالفعل. أوقفها الأول.")

        job_id = self.db.create_job(project_id, pages_total=max(len(target.start_urls), 1))
        # Persist the full job specification (audit C3/H1): an interrupted
        # job can be reconstructed from this + its job_queue checkpoint.
        self.db.set_job_spec(job_id, self._serialize_spec(
            target, fields, options, container, detail_config, source_profiles))
        worker = ScrapeJobWorker(self.db, job_id, project_id, target, fields, options, container, detail_config, source_profiles)
        thread = QThread()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)

        self._thread = thread
        self._worker = worker
        return job_id, worker

    @staticmethod
    def _serialize_spec(target: TargetConfig, fields: list[ExtractionField], options: ScrapeOptions,
                        container: Optional[dict], detail_config: Optional[dict],
                        source_profiles: Optional[list[dict]]) -> dict:
        spec = {
            "target": asdict(target),
            "options": {**asdict(options),
                        "fetcher_mode": getattr(options.fetcher_mode, "value", options.fetcher_mode)},
            "fields": [f.to_dict() for f in fields],
            "container": container,
            "detail_config": detail_config,
            "source_profiles": None,
        }
        if source_profiles:
            out_profiles = []
            for p in source_profiles:
                q = dict(p)
                fm = q.get("fetcher_mode")
                if isinstance(fm, FetcherMode):
                    q["fetcher_mode"] = fm.value
                if q.get("fields") is not None:
                    q["fields"] = [f.to_dict() if isinstance(f, ExtractionField) else f
                                   for f in q["fields"]]
                out_profiles.append(q)
            spec["source_profiles"] = out_profiles
        return spec

    def prepare_resume_job(self, job_id: int) -> tuple[int, ScrapeJobWorker]:
        """Rebuild a worker for an INTERRUPTED job from its persisted spec
        + queue checkpoint (audit C3/H1): the remaining 'pending' URLs are
        the exact remaining work, 'done' rows are never re-fetched, and
        lead dedupe guards the record side. Returns (job_id, worker) the
        same way prepare_job() does - connect UI signals, then call
        start_prepared_job()."""
        if self.is_running:
            raise RuntimeError("مهمة تانية شغالة بالفعل. أوقفها الأول.")
        spec = self.db.get_job_spec(job_id)
        if not spec:
            raise RuntimeError("المهمة دي محفوظة من نسخة أقدم - مفيش مواصفات محفوظة للاستئناف.")
        # rows that were mid-flight when the app died go back to pending -
        # their fetch never completed, so they are exactly the resume work
        self.db.queue_reset_in_progress(job_id)
        pending = self.db.queue_pending(job_id)
        if not pending:
            raise RuntimeError("مفيش روابط متبقية للاستئناف في المهمة دي.")

        target = TargetConfig(**spec["target"])
        target.start_urls = [u for u, _ in pending]
        od = dict(spec["options"])
        od["fetcher_mode"] = FetcherMode(od.get("fetcher_mode", "fast_http"))
        od["proxy"] = ProxyConfig(**od.get("proxy", {}))
        od["ai_extraction"] = AIExtractionConfig(**od.get("ai_extraction", {}))
        options = ScrapeOptions(**od)
        fields = [ExtractionField.from_dict(f) for f in spec.get("fields") or []]
        container = spec.get("container")
        detail_config = spec.get("detail_config")
        profiles = spec.get("source_profiles")
        if profiles:
            for p in profiles:
                fm = p.get("fetcher_mode")
                if fm:
                    p["fetcher_mode"] = FetcherMode(fm)
                if p.get("fields") is not None:
                    p["fields"] = [ExtractionField.from_dict(f) if isinstance(f, dict) else f
                                   for f in p["fields"]]

        worker = ScrapeJobWorker(self.db, job_id, None, target, fields, options,
                                 container, detail_config, profiles, resumed=True)
        self.db.mark_job_resumed(job_id)
        self.db.add_log(job_id, LogLevel.INFO.value,
                        f"استئناف المهمة من نقطة التوقف: {len(pending)} رابط متبقٍ")

        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._worker = worker
        return job_id, worker

    def start_prepared_job(self):
        """Actually start the thread built by prepare_job(). Call only
        after connecting every UI slot you need to the worker returned
        by prepare_job() - see that method's docstring for why."""
        if self._thread is not None:
            self._thread.start()

    def start_job(self, project_id: Optional[int], target: TargetConfig, fields: list[ExtractionField],
                  options: ScrapeOptions, container: Optional[dict] = None,
                  detail_config: Optional[dict] = None,
                  source_profiles: Optional[list[dict]] = None) -> tuple[int, ScrapeJobWorker]:
        """Back-compat convenience: prepare + start immediately, for
        callers that don't need to connect any UI signals first. Prefer
        prepare_job()/start_prepared_job() when the caller (like New
        Scrape) needs to attach log/progress/result listeners - see the
        race explained in prepare_job()'s docstring."""
        job_id, worker = self.prepare_job(project_id, target, fields, options, container, detail_config, source_profiles)
        self.start_prepared_job()
        return job_id, worker

    def stop(self):
        if self._worker:
            self._worker.request_stop()

    def pause(self, paused: bool):
        if self._worker:
            self._worker.request_pause(paused)

    def _on_thread_finished(self):
        self._thread = None
        self._worker = None
