"""
Job Manager: owns the background thread that actually runs a scrape, and
is the only bridge between the Qt UI and the (Qt-free) engine layer.

Architecture (spec section 12):
    UI -> JobManager -> ScrapeJobWorker -> scrapling_adapter -> Scrapling
                                        -> extractor
                                        -> Database (streamed results)

Runs on a QThread so the GUI event loop is never blocked by network I/O
or a browser call (spec section 13/29: "no UI freezing").
"""
from __future__ import annotations

import re
import time
import traceback
from collections import deque
from typing import Optional
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QThread, Signal

from app.core.engine import scrapling_adapter as engine
from app.core.engine import ai_extractor
from app.core.engine.extractor import extract_fields, extract_records, ExtractionError
from app.core.engine.qualifier import qualify_html
from app.core.models import ExtractionField, JobStatus, TargetConfig, ScrapeOptions, LogLevel
from app.core.storage.db import Database
from app.core.storage.secrets import SecretStore


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
                 source_profiles: Optional[list[dict]] = None):
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

            queue: deque[tuple[str, int]] = deque((u, 0) for u in self.target.start_urls)  # (url, depth)
            seen: set[str] = set(self.target.start_urls)
            pages_done = 0
            records_ok = 0
            records_failed = 0
            pages_total = max(len(queue), self.target.max_pages)
            first_page = True  # no delay before the very first fetch

            while queue and not self._stop_requested:
                while self._pause_requested and not self._stop_requested:
                    time.sleep(0.2)
                if self._stop_requested:
                    break
                if pages_done >= self.target.max_pages:
                    self._emit_log(LogLevel.INFO, f"تم الوصول للحد الأقصى للصفحات ({self.target.max_pages})")
                    break

                # "Delay between requests" (Advanced Options) was collected
                # from the UI (new_scrape.py's delay_spin -> options.delay_ms)
                # but never actually used anywhere in this loop - a real bug,
                # not just cosmetic: it's exactly what made a 500-page Yelp
                # run hammer yelp.com back-to-back with zero pacing between
                # requests, which is a big part of why Yelp's WAF started
                # returning HTTP 403 on nearly every request after the first
                # few dozen (see the 91/91-failed run this was reported
                # against). Runs on every iteration (not skipped on an error
                # continue below) so a page that just got 403'd doesn't
                # immediately get hammered again on the very next URL either.
                if not first_page and self.options.delay_ms > 0:
                    self._interruptible_sleep(self.options.delay_ms / 1000.0)
                first_page = False
                if self._stop_requested:
                    break  # Stop was clicked during the delay itself

                url, depth = queue.popleft()
                # Multi-source runs: pick this URL's own container/fields/
                # detail_config before fetching+extracting it - see the
                # source_profiles docstring in __init__ above. A no-op
                # (self.container/self.fields/self.detail_config end up
                # exactly what they already were) when source_profiles
                # isn't set, since _resolve_source() then just returns the
                # _base_* values it was seeded from.
                self.container, self.fields, self.detail_config = self._resolve_source(url)
                self._emit_log(LogLevel.INFO, f"جلب الصفحة: {url}")

                fetch_result, error = self._fetch_with_retries(url)

                if self._stop_requested:
                    break  # Stop was clicked mid-fetch - don't count this as a failed page

                pages_done += 1

                if error is not None:
                    records_failed += 1
                    self._emit_log(LogLevel.ERROR, f"فشل جلب {url}: {error}")
                    self.url_error.emit(url, error)
                    self.progress.emit(pages_done, pages_total, records_ok, records_failed)
                    continue

                self._emit_log(LogLevel.SUCCESS, f"تم الجلب، جاري الاستخراج: {url}")
                try:
                    records = self._extract(fetch_result.page)
                except ExtractionError as e:
                    records_failed += 1
                    self._emit_log(LogLevel.ERROR, f"فشل الاستخراج من {url}: {e}")
                    self.progress.emit(pages_done, pages_total, records_ok, records_failed)
                    continue

                for record in records:
                    if self.detail_config:
                        self._enrich_with_detail_page(record, url)
                    if self.options.auto_qualify_leads:
                        self._qualify_lead(record, url)
                    if self.options.owner_lookup_enabled:
                        self._lookup_owner_contact_info(record, url)
                    self.db.add_result(self.job_id, url, record)
                    records_ok += 1
                    self.result_ready.emit(record)
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
                self.progress.emit(pages_done, pages_total, records_ok, records_failed)

            final_status = JobStatus.STOPPED if self._stop_requested else JobStatus.COMPLETED
            self.db.finish_job(self.job_id, final_status.value)
            self.status_changed.emit(final_status.value)
            self._emit_log(
                LogLevel.SUCCESS if final_status == JobStatus.COMPLETED else LogLevel.WARNING,
                f"انتهت المهمة: {final_status.value} - {records_ok} سجل ناجح، {records_failed} خطأ",
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
            # Always reached, no matter what failed above - this is what
            # lets thread.quit() run (worker.finished -> thread.quit is
            # connected in JobManager.start_job) so the QThread actually
            # stops instead of hanging forever with Stop/pause no longer
            # doing anything.
            self.finished.emit(self.job_id)

    # --- helpers ---
    def _fetch_with_retries(self, url: str):
        last_error = None
        attempts = max(1, self.options.retries + 1)
        for attempt in range(1, attempts + 1):
            if self._stop_requested:
                return None, "أوقفه المستخدم"
            try:
                # should_stop lets a hung/slow fetch be interrupted within
                # ~0.25s of a Stop click, instead of only being checked
                # between whole retry attempts (which could be up to
                # timeout+15s apart) - that gap was why Stop looked broken.
                return engine.fetch_one(url, self.options, should_stop=lambda: self._stop_requested), None
            except engine.FetchCancelled:
                return None, "أوقفه المستخدم"
            except engine.FetchError as e:
                last_error = str(e.reason)
                if attempt < attempts:
                    self._emit_log(LogLevel.WARNING, f"إعادة محاولة {attempt}/{attempts - 1} لـ {url}: {last_error}")
                    self._interruptible_sleep(min(2 ** attempt, 10))
                    if self._stop_requested:
                        return None, "أوقفه المستخدم"
            except RuntimeError as e:  # Scrapling not installed
                return None, str(e)
        return None, last_error

    def _interruptible_sleep(self, seconds: float):
        """time.sleep() that bails early if Stop is clicked, so the
        exponential backoff between retries can't itself block Stop."""
        end = time.time() + seconds
        while time.time() < end and not self._stop_requested:
            time.sleep(min(0.2, max(0.0, end - time.time())))

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
        try:
            fr = engine.fetch_one(website_url, self.options, should_stop=lambda: self._stop_requested)
            html = engine.get_html(fr.page)
            text = engine.html_to_text(html)
            owner_data = ai_extractor.extract(provider, text, owner_fields, api_key)
        except Exception as e:
            self._emit_log(LogLevel.DEBUG, f"تعذر البحث عن بيانات مالك من {website_url}: {e}")
            return

        for key, value in owner_data.items():
            if value not in (None, ""):
                record[key] = value

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

    def _extract(self, page) -> list[dict]:
        if self.options.ai_extraction.enabled:
            return self._extract_with_ai(page)
        if self.container and self.container.get("selector"):
            return extract_records(page, self.container["selector"], self.container.get("type", "css"), self.fields)
        return [extract_fields(page, self.fields)]

    def _extract_with_ai(self, page) -> list[dict]:
        """No-selector extraction path: read the whole page as text and
        let the LLM fill in the requested field names. One record per
        page (AI Auto-Extract is aimed at "one business per page" targets
        like a company's own site or a directory profile page, not
        listing pages with many cards - use Custom Selector for those)."""
        ai_cfg = self.options.ai_extraction
        api_key = self._resolve_api_key(ai_cfg.provider)
        html = engine.get_html(page)
        text = engine.html_to_text(html)
        try:
            record = ai_extractor.extract(ai_cfg.provider, text, ai_cfg.field_names, api_key)
        except ai_extractor.AIExtractionError as e:
            raise ExtractionError("ai_extract", str(e)) from e
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
        self.db.add_log(self.job_id, level.value, message)
        self.log.emit(level.value, message)


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

        job_id = self.db.create_job(project_id, pages_total=max(len(target.start_urls), target.max_pages))
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
