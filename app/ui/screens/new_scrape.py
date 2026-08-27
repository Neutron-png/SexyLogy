from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QLineEdit, QTabWidget, QComboBox, QSpinBox, QCheckBox, QFormLayout,
    QScrollArea, QMessageBox, QFileDialog, QProgressBar, QSplitter, QToolBox,
    QTableView, QAbstractItemView, QListWidget, QListWidgetItem,
)

from app.core.models import (
    ExtractionField, TargetConfig, ScrapeOptions, ProxyConfig, FetcherMode, JobStatus, AIExtractionConfig,
)
from app.core.engine.nl_to_fields import generate_fields
from app.core.engine.ai_extractor import DEFAULT_FIELD_NAMES as DEFAULT_AI_FIELDS
from app.core.engine import scrapling_adapter as engine
from app.core.engine.builtin_templates import (
    generate_niche_urls, generate_niche_urls_yelp, generate_niche_urls_all_sources,
    RESULTS_PER_PAGE, YELP_RESULTS_PER_PAGE, YELP_CONTAINER, YELP_DETAIL_CONFIG,
    ICP_NICHES, CITY_POOL, MAX_URLS_ALL_CITIES,
    get_all_source_profiles,
)
from app.core.exports import exporter
from app.core.job_manager import JobManager
from app.core.storage.db import Database
from app.utils.validation import parse_url_list, validate_json_schema
from app.ui.dialogs.source_dialog import SourceDialog
from app.ui.dialogs.city_picker_dialog import CityPickerDialog
from app.ui.widgets.field_builder import FieldBuilder
from app.ui.widgets.log_panel import LogPanel
from app.ui.widgets.results_table import ResultsTableModel


def card(title: str) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    w.setObjectName("card")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 14, 16, 16)
    layout.setSpacing(10)
    if title:
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
    return w, layout


class NewScrapeScreen(QWidget):
    def __init__(self, db: Database, job_manager: JobManager, parent=None):
        super().__init__(parent)
        self.db = db
        self.job_manager = job_manager
        self.current_job_id: int | None = None
        self.results_model: ResultsTableModel | None = None
        # Set by _apply_niche_template() when the selected niche's source
        # needs a second per-lead fetch to fill in fields the listing page
        # doesn't have (currently: Yelp, for phone numbers - see
        # app/core/engine/builtin_templates.py's _YELP_DETAIL_CONFIG and
        # job_manager.py's _enrich_with_detail_page()). None for sources
        # that don't need it (yellowpages, or no niche template applied).
        self._active_detail_config: dict | None = None
        # Set only by a multi-source pick ("Load All Sources" button /
        # Quick Start's "All Sources" niche option - see SOURCE_PROFILES
        # in builtin_templates.py). When set, job_manager picks each
        # fetched URL's own container/fields/detail_config by matching
        # its domain against this list instead of using one fixed
        # selector set for every URL in the job - see
        # ScrapeJobWorker._resolve_source().
        self._active_source_profiles: list[dict] | None = None
        # Cities to restrict every generated search URL to - "خليني اقدر
        # احدد المدن اللي محتاجها". Defaults to the full CITY_POOL (every
        # box checked in the picker) - the previous, only, behavior.
        # Changed via _open_city_picker() below; read by _collect_cities().
        self._selected_cities: list[tuple[str, str]] = list(CITY_POOL)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ---- header ----
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("New Scrape")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Configure your scraping task")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.save_btn = QPushButton("Save Project")
        self.start_btn = QPushButton("▶ Start Scraping")
        self.start_btn.setObjectName("primaryButton")
        self.pause_btn = QPushButton("⏸ Pause")
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        for b in (self.save_btn, self.start_btn, self.pause_btn, self.stop_btn):
            header.addWidget(b)
        root.addLayout(header)

        self.save_btn.clicked.connect(self._save_project)
        self.start_btn.clicked.connect(self._start_scraping)
        self.pause_btn.clicked.connect(self._toggle_pause)
        self.stop_btn.clicked.connect(self._stop_scraping)

        # ---- scrollable configuration area ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        config_layout.setSpacing(14)
        scroll.setWidget(config_widget)

        config_layout.addWidget(self._build_quick_start_section())
        config_layout.addWidget(self._build_sources_section())
        config_layout.addWidget(self._build_target_section())
        config_layout.addWidget(self._build_extraction_section())

        # Fetcher mode / proxy / headers are exactly the settings a
        # non-technical user has no reason to touch - LOGY's defaults
        # (Fast/HTTP, no proxy, sensible timeouts) work for most targets.
        # Hidden by default; one checkbox reveals them for power users.
        self.advanced_toggle_chk = QCheckBox("Show Advanced Options (fetcher mode, proxy, headers, timeouts...)")
        self.advanced_toggle_chk.toggled.connect(self._toggle_advanced_sections)
        config_layout.addWidget(self.advanced_toggle_chk)

        self.options_section = self._build_options_section()
        self.proxy_section = self._build_proxy_section()
        config_layout.addWidget(self.options_section)
        config_layout.addWidget(self.proxy_section)
        self.options_section.setVisible(False)
        self.proxy_section.setVisible(False)

        config_layout.addStretch(1)

        # ---- live run panel (hidden until a job starts) ----
        self.run_panel = self._build_run_panel()
        self.run_panel.setVisible(False)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(self.run_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

    # ------------------------------------------------------------------
    # QUICK START (for non-technical users): pick your ICP niche, LOGY
    # fills in the output columns and turns on auto-qualification for you.
    # You still need to paste in target URLs and, for now, the CSS/XPath
    # selectors for those URLs (the click-to-select Selector Assistant
    # isn't built yet - see README "Known gaps"). This section removes
    # every OTHER decision a beginner would otherwise have to make.
    # ------------------------------------------------------------------
    def _build_quick_start_section(self) -> QWidget:
        w, layout = card("Quick Start - pick your niche")
        note = QLabel(
            "Choose the type of business you're prospecting. LOGY sets the output columns "
            "(name, phone, website, address, city) and turns on automatic lead qualification "
            "(flags businesses with no website / a weak website) for you."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        row = QHBoxLayout()
        self.niche_combo = QComboBox()
        self.niche_combo.addItem("Choose a niche...", None)
        # ONE entry per niche now - "شيل ان يبقى فيه 3 اوبشن لكل نيش ...
        # من غير ما تقول في الاسم ان هو كذا + كذا + كذا": the user
        # explicitly does not want to choose yellowpages-only vs Yelp-only
        # vs "All Sources" per niche, and does not want the combo label to
        # spell out which sources are combined. Every niche pick now
        # always combines all 3 sources (yellowpages + Yelp + thumbtack -
        # see generate_niche_urls_all_sources() in builtin_templates.py)
        # automatically - the combo's data is just the bare niche name
        # string, consumed directly by _apply_niche_template() below.
        for niche, fee, _term in ICP_NICHES:
            fee_tag = f"  ({fee})" if fee else ""
            self.niche_combo.addItem(f"{niche}{fee_tag}", niche)
        apply_btn = QPushButton("Use this niche")
        apply_btn.setObjectName("primaryButton")
        apply_btn.clicked.connect(self._apply_niche_template)
        row.addWidget(self.niche_combo, 1)
        row.addWidget(apply_btn)
        layout.addLayout(row)

        # "عايز الف ولا الفين" - let the user say how many leads they want
        # instead of a hard-coded 2-city, ~50-lead default. LOGY pages
        # through yellowpages.com (&page=2, &page=3, ...) across more
        # cities to cover it - see generate_niche_urls() in
        # app/core/engine/builtin_templates.py for exactly how the URL
        # list is sized from this number.
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("How many leads (approx.):"))
        self.target_results_spin = QSpinBox()
        # Ceiling matches generate_niche_urls()'s own ceiling
        # (MAX_URLS_ALL_CITIES = all 100 cities x MAX_PAGES_PER_CITY pages
        # x RESULTS_PER_PAGE/page) - raising the spinbox past what the
        # generator can ever actually produce would just silently cap
        # back down with no explanation, which is exactly the "عايزة
        # يسيرش التوب 100 مدينة" complaint in a different shape.
        self.target_results_spin.setRange(RESULTS_PER_PAGE, MAX_URLS_ALL_CITIES * RESULTS_PER_PAGE)
        self.target_results_spin.setSingleStep(RESULTS_PER_PAGE)
        # 6000 is the smallest value that reaches ALL top 100 cities' page
        # 1 in EVERY mode by default, including "All Sources" (which
        # splits this number in half between yellowpages/Yelp before each
        # source converts its own half to a page count - halving anything
        # below 6000 would leave yellowpages short of all 100 cities).
        # "عايزة يسيرش التوب 100 مدينة" shouldn't require the user to
        # already know that math just to get full coverage on the first
        # try.
        self.target_results_spin.setValue(6000)
        target_row.addWidget(self.target_results_spin)

        # "خليني اقدر احدد المدن اللي محتاجها و بالتالي دا ينطبق على
        # اللينكات اللي هتطلع برضة تبقى مخصصة للمدينة دي بس" + "المدن تبقى
        # في سلايدر فيه كل المدن اللي بنشتغل عليها + سيرش بار فيها" -
        # restrict every generated URL (any source, niche picked from Quick
        # Start or from the buttons in the Target section below) to ONLY
        # the cities checked in the "Choose Cities..." picker dialog (see
        # app/ui/dialogs/city_picker_dialog.py - a checkable, live-
        # searchable list of the full CITY_POOL, opened via
        # _open_city_picker() below), instead of always spreading across
        # the full top-100 CITY_POOL. self._selected_cities (set in
        # __init__) holds the current pick; _collect_cities() below is
        # what every URL-generating call site in this file goes through.
        cities_row = QHBoxLayout()
        cities_row.addWidget(QLabel("Cities:"))
        choose_cities_btn = QPushButton("Choose Cities...")
        choose_cities_btn.clicked.connect(self._open_city_picker)
        choose_cities_btn.setToolTip(
            "Restricts every generated search URL (Quick Start and the Target section's "
            "'Load ... Search Links' buttons) to just the cities you check here, instead of "
            "always spreading across all top 100."
        )
        cities_row.addWidget(choose_cities_btn)
        self.cities_summary_label = QLabel("")
        self.cities_summary_label.setStyleSheet("color: #8B95A7; font-size: 11px;")
        cities_row.addWidget(self.cities_summary_label)
        cities_row.addStretch(1)
        self._update_cities_summary()

        target_hint = QLabel(
            f"(LOGY covers every city's first page before paging deeper into any one city - set this "
            f"to ~{len(CITY_POOL) * RESULTS_PER_PAGE} to reach all top {len(CITY_POOL)} cities at least "
            "once, or higher to also page deeper into each. Actual count depends on how many real "
            "businesses exist for this niche.)"
        )
        target_hint.setWordWrap(True)
        target_hint.setStyleSheet("color: #8B95A7; font-size: 11px;")
        target_row.addWidget(target_hint, 1)
        layout.addLayout(target_row)
        layout.addLayout(cities_row)

        self.auto_qualify_chk = QCheckBox("Auto-qualify leads (flag weak/missing websites automatically)")
        self.auto_qualify_chk.setToolTip(
            "For each result with a website field, LOGY fetches that site and flags it "
            "'no website' / 'weak site' / 'strong site' based on real page signals "
            "(HTTPS, mobile-friendliness, SEO basics). No selector knowledge needed."
        )
        layout.addWidget(self.auto_qualify_chk)

        # 'اسم البيزنيس + الميل بتاع الاونر + اللينكد ان بروفايل بتاع
        # الاونر + رقم تليفون الاونر' - Yelp/yellowpages listings never
        # publish this (only business-level info, at most a business
        # phone) - the only place it's legitimately public is the lead's
        # OWN website, so this does one extra AI-read fetch per lead
        # against its "website" field. See job_manager's
        # _lookup_owner_contact_info() docstring for exactly why this
        # isn't the same thing as automated LinkedIn people-search (which
        # this project still does not do).
        self.owner_lookup_chk = QCheckBox("Look up owner contact info from each lead's website (AI, needs API key)")
        self.owner_lookup_chk.setToolTip(
            "For each result with a website field, LOGY fetches that site and asks the AI model "
            "(same provider/key as AI Auto-Extract below) to fill in owner_name / owner_email / "
            "owner_phone / owner_linkedin_if_published from whatever that site actually publishes "
            "(e.g. an 'About Us' or 'Meet the Owner' page) - never invented, and never a LinkedIn "
            "search on LOGY's own initiative. Adds one extra fetch + API call per lead, so a large "
            "run will be slower and cost more API usage."
        )
        layout.addWidget(self.owner_lookup_chk)

        self.quick_start_status = QLabel("")
        self.quick_start_status.setStyleSheet("color: #22C55E; font-size: 11px;")
        layout.addWidget(self.quick_start_status)
        return w

    def _toggle_advanced_sections(self, checked: bool):
        self.options_section.setVisible(checked)
        self.proxy_section.setVisible(checked)

    # ------------------------------------------------------------------
    # SOURCES - "خليني اقدر من جوا اضيف مصادر جديدة": lets the user
    # define a new scraping source (domain + container/fields selectors,
    # optionally a 2nd-fetch detail_config) from inside the app instead
    # of editing app/core/engine/builtin_templates.py by hand. Saved
    # sources are stored via Database.create_custom_source() and merged
    # with the built-ins (yellowpages, yelp, thumbtack) by
    # get_all_source_profiles() - see _get_source_profiles() above -
    # everywhere a multi-source ("All Sources") run is built.
    # ------------------------------------------------------------------
    def _build_sources_section(self) -> QWidget:
        w, layout = card("Sources")
        note = QLabel(
            "Sites LOGY can pull leads from in an 'All Sources' run. yellowpages.com, yelp.com and "
            "thumbtack.com are all captured live and ready to use. Note: thumbtack.com never publicly "
            "shows a phone number or website on its pages (contact only happens through its own "
            "gated 'Message' flow), so its leads carry business name + rating + a link back to its "
            "Thumbtack profile only - not a selector gap, just what that site actually publishes. Add "
            "any other site of your own below."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        self.sources_list = QListWidget()
        self.sources_list.setFixedHeight(120)
        layout.addWidget(self.sources_list)

        row = QHBoxLayout()
        add_btn = QPushButton("+ Add Source")
        add_btn.setObjectName("primaryButton")
        add_btn.clicked.connect(self._add_source)
        self.edit_source_btn = QPushButton("Edit")
        self.edit_source_btn.clicked.connect(self._edit_source)
        self.delete_source_btn = QPushButton("Delete")
        self.delete_source_btn.setObjectName("dangerButton")
        self.delete_source_btn.clicked.connect(self._delete_source)
        row.addWidget(add_btn)
        row.addWidget(self.edit_source_btn)
        row.addWidget(self.delete_source_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self._refresh_sources_list()
        return w

    def _refresh_sources_list(self):
        self.sources_list.clear()
        custom_rows = self.db.list_custom_sources()
        for profile in self._get_source_profiles():
            custom_row = next(
                (r for r in custom_rows if r["name"] == profile["name"] and r["domain"] == profile["domain"]),
                None,
            )
            badge = "" if profile.get("verified", True) else "  ⚠ selectors not confirmed"
            kind = "custom" if custom_row else "built-in"
            item = QListWidgetItem(f"{profile['name']}  ({profile['domain']})  [{kind}]{badge}")
            item.setData(Qt.ItemDataRole.UserRole, custom_row["id"] if custom_row else None)
            self.sources_list.addItem(item)

    def _add_source(self):
        dialog = SourceDialog(self)
        if dialog.exec():
            self.db.create_custom_source(
                dialog.result_name, dialog.result_domain, dialog.result_container,
                dialog.result_fields, dialog.result_detail_config,
            )
            self._refresh_sources_list()

    def _selected_custom_source_id(self) -> int | None:
        item = self.sources_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _edit_source(self):
        source_id = self._selected_custom_source_id()
        if source_id is None:
            QMessageBox.information(self, "Edit Source", "اختار مصدر مضاف من عندك الأول (المصادر الأساسية مش قابلة للتعديل من هنا).")
            return
        row = next(r for r in self.db.list_custom_sources() if r["id"] == source_id)
        existing = {
            "name": row["name"], "domain": row["domain"],
            "container": json.loads(row["container_json"]),
            "fields": json.loads(row["fields_json"]),
            "detail_config": json.loads(row["detail_config_json"]) if row["detail_config_json"] else None,
        }
        dialog = SourceDialog(self, existing=existing)
        if dialog.exec():
            self.db.update_custom_source(
                source_id, dialog.result_name, dialog.result_domain, dialog.result_container,
                dialog.result_fields, dialog.result_detail_config,
            )
            self._refresh_sources_list()

    def _delete_source(self):
        source_id = self._selected_custom_source_id()
        if source_id is None:
            QMessageBox.information(self, "Delete Source", "اختار مصدر مضاف من عندك الأول (المصادر الأساسية مش قابلة للحذف).")
            return
        reply = QMessageBox.question(self, "Delete Source", "متأكد إنك عايز تمسح المصدر ده؟")
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_custom_source(source_id)
            self._refresh_sources_list()

    def _open_city_picker(self):
        dialog = CityPickerDialog(self, selected=self._selected_cities)
        if dialog.exec():
            self._selected_cities = dialog.selected_cities()
            self._update_cities_summary()

    def _update_cities_summary(self):
        n = len(self._selected_cities)
        total = len(CITY_POOL)
        self.cities_summary_label.setText(f"All {total} cities" if n == total else f"{n} of {total} cities")

    def _collect_cities(self) -> list[tuple[str, str]] | None:
        """Cities picked via the 'Choose Cities...' dialog (see
        _open_city_picker()) - defaults to the full CITY_POOL (every box
        checked) until the user changes the selection. Every call site
        in this file that generates niche URLs goes through this so the
        city restriction applies everywhere consistently (Quick Start's
        'Use this niche', the Target section's 'Load ... Search Links'
        buttons, and 'All Sources')."""
        return self._selected_cities or None

    def _get_source_profiles(self) -> list[dict]:
        """Built-in SOURCE_PROFILES (yellowpages, yelp, thumbtack) plus
        whatever the user added via the Sources card's '+ Add Source' -
        see builtin_templates.get_all_source_profiles(). Every call site
        that used to import SOURCE_PROFILES directly for a multi-source
        run now goes through this instead, so a user-added source
        participates in 'All Sources' / 'Load All Sources (combined)'
        with no other wiring."""
        return get_all_source_profiles(self.db)

    def _apply_niche_template(self):
        # niche_combo's data is now always just the bare niche-name string
        # (see _build_quick_start_section() above) - every pick always
        # combines all 3 sources automatically, so this just delegates to
        # the combined-sources logic. No more per-niche source choice.
        niche_name = self.niche_combo.currentData()
        if niche_name is None:
            return
        self._apply_all_sources_niche(niche_name)

    def _apply_all_sources_niche(self, niche_name: str):
        """Every Quick Start niche pick now combines yellowpages.com +
        Yelp + thumbtack.com URLs for this one niche into a SINGLE job's
        start_urls (see generate_niche_urls_all_sources()), instead of the
        user running one source, exporting, running another, and merging
        CSVs by hand - and without a separate "All Sources" option to
        choose (see _apply_niche_template() above and "شيل ان يبقى فيه 3
        اوبشن لكل نيش ... المصادر من ال 3 مواقع ... بشكل تلقائي"). The
        Field Builder / container inputs below are only the FALLBACK
        job_manager uses for a URL that matches neither known domain
        (shouldn't happen with this generator's own output) - the real
        per-URL selector choice happens at fetch time in
        job_manager.ScrapeJobWorker._resolve_source(), keyed off
        self._active_source_profiles set below."""
        source_profiles = self._get_source_profiles()
        yp_profile = next(p for p in source_profiles if p["name"] == "yellowpages")
        yelp_profile = next(p for p in source_profiles if p["name"] == "yelp")
        thumbtack_profile = next((p for p in source_profiles if p["name"] == "thumbtack"), None)

        combined_fields = list(yp_profile["fields"])
        existing_names = {f.name for f in combined_fields}
        for extra_profile in (yelp_profile, thumbtack_profile):
            if not extra_profile:
                continue
            for f in extra_profile["fields"]:
                if f.name not in existing_names:
                    combined_fields.append(f)
                    existing_names.add(f.name)
        self.field_builder.load_fields(combined_fields)

        self.container_selector_input.setText(yp_profile["container"]["selector"])
        idx = self.container_type_combo.findText(yp_profile["container"].get("type", "css"))
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)

        self.auto_qualify_chk.setChecked(True)
        self._active_detail_config = None  # per-URL detail_config comes from source_profiles instead
        # ALL source profiles (built-in + custom), not just the 3 named
        # above, so a user-added source (e.g. one pasted in via its OWN
        # start_urls further down in the Target box) still resolves
        # correctly at fetch time even though
        # generate_niche_urls_all_sources() below only knows how to
        # generate URLs for yellowpages/Yelp/thumbtack themselves.
        self._active_source_profiles = source_profiles
        self._apply_yelp_anti_block_settings()  # this run includes yelp.com URLs too

        target_count = self.target_results_spin.value()
        start_urls = generate_niche_urls_all_sources(niche_name, target_count, cities=self._collect_cities())
        if start_urls:
            self.urls_input.setPlainText("\n".join(start_urls))
            self.max_pages_spin.setValue(max(len(start_urls), self.max_pages_spin.value()))

        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        if start_urls:
            yp_count = sum(1 for u in start_urls if "yellowpages.com" in u)
            yelp_count = sum(1 for u in start_urls if "yelp.com" in u)
            thumbtack_count = len(start_urls) - yp_count - yelp_count
            self.quick_start_status.setText(
                f"✓ '{niche_name}' جاهز - {len(start_urls)} رابط بحث ({yp_count} من yellowpages.com + "
                f"{yelp_count} من yelp.com + {thumbtack_count} من thumbtack.com) في نفس القائمة تحت. "
                "كل رابط هياخد السلكتور بتاعه الصح أوتوماتيك حسب مصدره - مش محتاج تشغلهم واحد واحد ولا "
                "تدمج نتايجهم بنفسك. ملحوظة: ليدز thumbtack.com هتيجي فيها اسم البيزنس + التقييم + "
                "لينك بروفايل بس (مفيش تليفون ولا موقع - الموقع نفسه مش بينشرهم للعامة أصلاً)، وبعض "
                "النيتشات (زي الأطباء/المحامين/وكلاء السيارات) ممكن مايكونش ليها تصنيف حقيقي في "
                "thumbtack فتطلع صفر ليدز منه بس المصدرين التانيين هيغطوها عادي. دوس Start Scraping "
                "على طول."
            )
        else:
            self.quick_start_status.setText(
                f"تعذر توليد روابط لـ '{niche_name}' - جرب نيتش تاني أو قلل عدد الليدز المطلوب."
            )

    # ------------------------------------------------------------------
    # STEP 1: TARGET
    # ------------------------------------------------------------------
    def _build_target_section(self) -> QWidget:
        w, layout = card("Target")

        self.urls_input = QPlainTextEdit()
        self.urls_input.setPlaceholderText("https://example.com\nhttps://example.com/products\nhttps://example.com/about")
        self.urls_input.setFixedHeight(90)
        layout.addWidget(self.urls_input)

        row = QHBoxLayout()
        self.url_count_label = QLabel("0 URLs")
        self.url_count_label.setStyleSheet("color: #8B95A7; font-size: 12px;")
        example_btn = QPushButton("Load Example Test Sites")
        real_btn = QPushButton("Load Real Directory Search Links")
        yelp_btn = QPushButton("Load Yelp Search Links")
        all_sources_btn = QPushButton("Load All Sources (combined)")
        houzz_btn = QPushButton("Load Houzz Search Links (needs AI API key)")
        import_btn = QPushButton("Import from TXT/CSV")
        clear_btn = QPushButton("Clear")
        example_btn.clicked.connect(self._load_example_sites)
        real_btn.clicked.connect(self._load_real_directory_links)
        yelp_btn.clicked.connect(self._load_yelp_directory_links)
        all_sources_btn.clicked.connect(self._load_all_sources_directory_links)
        houzz_btn.clicked.connect(self._load_houzz_directory_links)
        import_btn.clicked.connect(self._import_urls)
        clear_btn.clicked.connect(lambda: self.urls_input.setPlainText(""))
        self.urls_input.textChanged.connect(self._update_url_count)
        row.addWidget(self.url_count_label)
        row.addStretch(1)
        row.addWidget(example_btn)
        row.addWidget(real_btn)
        row.addWidget(yelp_btn)
        row.addWidget(all_sources_btn)
        row.addWidget(houzz_btn)
        row.addWidget(import_btn)
        row.addWidget(clear_btn)
        layout.addLayout(row)

        example_note = QLabel(
            "'Load Example Test Sites' fills in public practice sites built specifically for testing "
            "scrapers (not real leads) - use them to confirm LOGY's pipeline works end to end before "
            "pointing it at a real target. 'Load Real Directory Search Links' fills in live "
            "yellowpages.com search results for 7 of your ICP niches (verified reachable, real listings) "
            "AND the matching Custom Selector / 'Repeat over' - no Inspect Element needed, just load and "
            "run. 'Load Yelp Search Links' does the same thing but from yelp.com instead (adds a 2nd "
            "fetch per lead to pull the phone number from each business's Yelp page). 'Load All Sources "
            "(combined)' puts BOTH sites' links in this same box at once and runs them as one job - no "
            "need to run one source, export, then run the other and merge by hand. 'Load Houzz Search "
            "Links' is different from the rest: Houzz's markup has no stable CSS hooks at all (fully "
            "randomized class names on every deploy), so it can't use Custom Selector like the others - "
            "it switches this job to AI Auto-Extract instead, which needs an Anthropic or OpenAI API key "
            "saved on the API Keys screen first (LOGY will warn you and refuse to start if none is set). "
            "For the full 15-niche list (any of the free modes, more cities per niche) use Quick Start "
            "above instead."
        )
        example_note.setWordWrap(True)
        example_note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(example_note)

        adv = QFormLayout()
        self.same_domain_chk = QCheckBox("Same domain only")
        self.same_domain_chk.setChecked(True)
        self.follow_links_chk = QCheckBox("Follow links")
        self.max_pages_spin = QSpinBox()
        self.max_pages_spin.setRange(1, 100000)
        self.max_pages_spin.setValue(50)
        self.max_depth_spin = QSpinBox()
        self.max_depth_spin.setRange(0, 20)
        self.max_depth_spin.setValue(1)
        self.robots_chk = QCheckBox("Respect robots.txt")
        self.robots_chk.setChecked(True)
        self.include_patterns_input = QLineEdit()
        self.include_patterns_input.setPlaceholderText("*/products/* (comma-separated)")
        self.exclude_patterns_input = QLineEdit()
        self.exclude_patterns_input.setPlaceholderText("*/login/* (comma-separated)")

        adv.addRow(self.same_domain_chk, self.follow_links_chk)
        adv.addRow("Max pages", self.max_pages_spin)
        adv.addRow("Max depth", self.max_depth_spin)
        adv.addRow(self.robots_chk)
        adv.addRow("Include patterns", self.include_patterns_input)
        adv.addRow("Exclude patterns", self.exclude_patterns_input)
        layout.addLayout(adv)
        return w

    def _update_url_count(self):
        valid, invalid = parse_url_list(self.urls_input.toPlainText())
        text = f"{len(valid)} URLs"
        if invalid:
            text += f"  ·  {len(invalid)} invalid line(s) will be ignored"
        self.url_count_label.setText(text)

    def _import_urls(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import URLs", "", "Text/CSV (*.txt *.csv)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        current = self.urls_input.toPlainText()
        self.urls_input.setPlainText((current + "\n" + content).strip())

    # Public sites built and published specifically so scraper developers
    # have something safe to practice against - not business directories,
    # not real leads. This is a fast way to prove LOGY's fetch -> extract
    # -> export pipeline actually works before pointing it at a real,
    # unverified target (which is where LOGY previously hung on LinkedIn).
    EXAMPLE_TEST_SITES = [
        "https://quotes.toscrape.com/",
        "https://books.toscrape.com/",
        "https://scrapeme.live/shop/",
        "https://webscraper.io/test-sites/e-commerce/allinone",
        "https://scrapethissite.com/pages/simple/",
        "https://scrapethissite.com/pages/forms/",
        "https://the-internet.herokuapp.com/",
    ]

    def _load_example_sites(self):
        self.urls_input.setPlainText("\n".join(self.EXAMPLE_TEST_SITES))
        self._active_detail_config = None
        self._active_source_profiles = None
        QMessageBox.information(
            self, "Example Test Sites",
            "دي مواقع تجربة عامة اتعملت أصلاً عشان مطوري الـ scrapers يتمرنوا عليها - مش مصادر ليدز حقيقية.\n\n"
            "استخدمها تتأكد إن LOGY شغال من أول لآخر (fetch → extract → export)، وبعدين حط رابط حقيقي "
            "لموقعك المستهدف الفعلي.",
        )

    # Live yellowpages.com search-results pages for 7 of the user's own
    # ICP niches, across US cities over 100k population. Superpages.com
    # was used originally, but it started returning a Cloudflare "Sorry,
    # you have been blocked" wall to real browser traffic (confirmed via
    # a live browser test on 2026-08-19, not just an automated fetch), so
    # it was dropped in favor of yellowpages.com, which was verified
    # reachable and returning real business listings via a live browser
    # session on the same date (e.g. "Athena Pools LLC", (512) 914-0554,
    # athenapools.com - pool builders, Austin TX). Unlike the old list,
    # these ship WITH working "Repeat over" + field selectors - see
    # app/core/engine/builtin_templates.py. Picking a niche from Quick
    # Start above does this automatically; this button is a fallback for
    # loading extra URLs without going through Quick Start.
    REAL_DIRECTORY_SEARCH_LINKS = [
        "https://www.yellowpages.com/search?search_terms=pool+builders&geo_location_terms=Austin%2C+TX",
        "https://www.yellowpages.com/search?search_terms=solar+installers&geo_location_terms=Phoenix%2C+AZ",
        "https://www.yellowpages.com/search?search_terms=foundation+repair&geo_location_terms=Dallas%2C+TX",
        "https://www.yellowpages.com/search?search_terms=kitchen+remodeling&geo_location_terms=Denver%2C+CO",
        "https://www.yellowpages.com/search?search_terms=water+damage+restoration&geo_location_terms=Tampa%2C+FL",
        "https://www.yellowpages.com/search?search_terms=commercial+painters&geo_location_terms=Charlotte%2C+NC",
        "https://www.yellowpages.com/search?search_terms=bathroom+remodeling&geo_location_terms=Sacramento%2C+CA",
    ]

    # Same container/field selectors LOGY prefills for the ICP Quick Start
    # niches (app/core/engine/builtin_templates.py) - kept here too so this
    # fallback button also loads a working Custom Selector, not an empty one.
    _REAL_DIRECTORY_CONTAINER = {"selector": ".result", "type": "css"}

    def _current_niche_name_from_combo(self) -> str | None:
        """Bare niche name (e.g. 'Pool Builders') for whatever is
        currently picked in Quick Start's niche_combo above, or None if
        it's still on 'Choose a niche...'. niche_combo's data is now
        always just the bare niche-name string (see
        _build_quick_start_section() above) - lets the Target section's
        "Load ... Search Links" buttons below generate a FULL top-100-city
        link list for whichever niche is already selected, instead of
        always falling back to the small fixed 7-niche demo."""
        return self.niche_combo.currentData()

    def _apply_yelp_anti_block_settings(self):
        """A real 500-page Yelp run reported back HTTP 403 on ~everything
        past the first few dozen pages (0 records from 91 fetched pages) -
        Yelp's bot-detection clearly does more than check headers at any
        real volume, and FAST_HTTP (a plain HTTP request, options.headers
        + stealthy_headers=True but no actual browser fingerprint at all)
        isn't enough past a small number of requests. Two changes, applied
        together whenever a job includes Yelp URLs:
        1. Switch Fetcher Mode to Stealth Browser - a real, harder-to-
           fingerprint browser context (scrapling_adapter.fetch_one()'s
           STEALTH_BROWSER branch), instead of a raw HTTP request.
        2. Set a real delay between requests - "Delay between requests" in
           Advanced Options was being collected from the UI but never
           actually applied anywhere in job_manager's fetch loop (a real
           bug, now fixed there too) - hammering yelp.com with zero pacing
           between hundreds of requests is exactly what a WAF is built to
           catch.
        Neither is a guarantee Yelp won't still block a very large run -
        Yelp actively defends against scraping - but this is a materially
        better chance than what just got 403'd on nearly everything."""
        idx = self.fetcher_combo.findData(FetcherMode.STEALTH_BROWSER)
        if idx >= 0:
            self.fetcher_combo.setCurrentIndex(idx)
        self.delay_spin.setValue(max(2000, self.delay_spin.value()))

    def _load_niche_single_source(self, niche_name: str, source: str) -> list[str]:
        """Shared by _load_real_directory_links() / _load_yelp_directory_links()
        for the case a niche IS already selected in Quick Start above:
        generate that ONE niche's full top-100-city link list for just
        this one source ('yellowpages' or 'yelp'), using the "How many
        leads" control's value - the same generator Quick Start's "Use
        this niche" uses, just reachable directly from the Target section
        without an extra click. 'دلوقيت الاقي 200 لينك في الكونتينر' -
        the small fixed-city demo lists below are for when NO niche is
        picked yet (a quick 'does this even work' check), not the real
        per-niche generator."""
        profile = next(p for p in self._get_source_profiles() if p["name"] == source)
        self.field_builder.load_fields(profile["fields"])
        self.container_selector_input.setText(profile["container"]["selector"])
        idx = self.container_type_combo.findText(profile["container"].get("type", "css"))
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)
        self._active_detail_config = profile.get("detail_config")
        self._active_source_profiles = None  # single source - no per-URL resolution needed
        self.auto_qualify_chk.setChecked(True)
        if source == "yelp":
            self._apply_yelp_anti_block_settings()

        target_count = self.target_results_spin.value()
        cities = self._collect_cities()
        start_urls = (
            generate_niche_urls_yelp(niche_name, target_count, cities=cities) if source == "yelp"
            else generate_niche_urls(niche_name, target_count, cities=cities)
        )
        if start_urls:
            self.urls_input.setPlainText("\n".join(start_urls))
            self.max_pages_spin.setValue(max(len(start_urls), self.max_pages_spin.value()))
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        return start_urls

    def _load_real_directory_links(self):
        # A niche is already picked in Quick Start above - generate ITS
        # full top-100-city yellowpages.com list instead of the small
        # fixed 7-niche demo below (see _current_niche_name_from_combo()'s
        # docstring - this is the fix for "100 مدينة يعني الاقي 200 لينك
        # ... انما ال 14 لينك دول اعمل بيهم ايه؟").
        niche_name = self._current_niche_name_from_combo()
        if niche_name:
            urls = self._load_niche_single_source(niche_name, "yellowpages")
            QMessageBox.information(
                self, "Real Directory Search Links",
                f"'{niche_name}' مختار في Quick Start فوق - عشان كدا اتحط {len(urls)} رابط حقيقي من "
                "yellowpages.com يغطوا أول صفحة لكل الـ100 مدينة (وأكتر لو 'How many leads' مرفوع أعلى)، "
                "مش الـ7 نيتشات التجريبية. auto-qualify اتفعّل معاهم تلقائي.\n\n"
                "دوس Start Scraping على طول.",
            )
            return
        self.urls_input.setPlainText("\n".join(self.REAL_DIRECTORY_SEARCH_LINKS))
        self.container_selector_input.setText(self._REAL_DIRECTORY_CONTAINER["selector"])
        idx = self.container_type_combo.findText(self._REAL_DIRECTORY_CONTAINER["type"])
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)
        # yellowpages.com has no 2nd-fetch enrichment step - clear any
        # detail_config a previous "Load Yelp Search Links" click may have
        # set, otherwise a yellowpages run would wastefully try to enrich
        # each result from a (non-existent) yelp_profile_url field.
        self._active_detail_config = None
        self._active_source_profiles = None  # single source - no per-URL resolution needed
        self.auto_qualify_chk.setChecked(True)
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        QMessageBox.information(
            self, "Real Directory Search Links",
            "دول 7 لينكات حقيقية اتأكدت إنها شغالة دلوقتي (yellowpages.com، اتفحصت ببروزر حقيقي مش أداة "
            "آلية بس) وبترجع نتايج فعلية لـ 7 من النيتشات بتاعتك - ده اختبار سريع لمدينة واحدة لكل نيتش، "
            "مش تغطية الـ100 مدينة.\n\n"
            "عشان تغطية كاملة لنيتش واحد بس (كل الـ100 مدينة)، اختار النيتش من Quick Start فوق الأول "
            "ثم دوس الزرار ده تاني - هيتحط لينكات النيتش دا بس على الـ100 مدينة بدل السبعة دول.\n\n"
            "الـ 'Repeat over' وسلكتورات الحقول (business_name / phone / website / address / city) "
            "اتحطوا أوتوماتيك في تاب Custom Selector - مش محتاج تعمل Inspect Element ولا تكتب حاجة. "
            "auto-qualify اتفعّل تلقائي. دوس Start Scraping على طول.",
        )

    # Live yelp.com search-results pages for 7 of the user's own ICP
    # niches (same 7 as REAL_DIRECTORY_SEARCH_LINKS above, so the two
    # buttons are directly comparable). Yelp's search-results page has no
    # phone number field at all - that's why _load_yelp_directory_links()
    # below also sets self._active_detail_config to YELP_DETAIL_CONFIG,
    # which makes the job do a 2nd fetch per lead against each business's
    # own Yelp page to pull the phone via regex (see
    # job_manager.py's _enrich_with_detail_page()). Without setting that,
    # this button would load working URLs + selectors but silently drop
    # phone numbers from every result.
    YELP_REAL_DIRECTORY_SEARCH_LINKS = [
        "https://www.yelp.com/search?find_desc=Pool+Builders&find_loc=Austin%2C+TX",
        "https://www.yelp.com/search?find_desc=Solar+Installers&find_loc=Phoenix%2C+AZ",
        "https://www.yelp.com/search?find_desc=Foundation+Repair&find_loc=Dallas%2C+TX",
        "https://www.yelp.com/search?find_desc=Kitchen+Remodeling&find_loc=Denver%2C+CO",
        "https://www.yelp.com/search?find_desc=Water+Damage+Restoration&find_loc=Tampa%2C+FL",
        "https://www.yelp.com/search?find_desc=Commercial+Painters&find_loc=Charlotte%2C+NC",
        "https://www.yelp.com/search?find_desc=Bathroom+Remodeling&find_loc=Sacramento%2C+CA",
    ]

    def _load_yelp_directory_links(self):
        niche_name = self._current_niche_name_from_combo()
        if niche_name:
            urls = self._load_niche_single_source(niche_name, "yelp")
            QMessageBox.information(
                self, "Yelp Search Links",
                f"'{niche_name}' مختار في Quick Start فوق - عشان كدا اتحط {len(urls)} رابط حقيقي من "
                "yelp.com يغطوا أول صفحة لكل الـ100 مدينة، مش الـ7 نيتشات التجريبية. فتش رقم التليفون "
                "التاني اتفعّل، وكمان auto-qualify.\n\n"
                "⚠️ يلب بيحظر الطلبات الكتير بسهولة (اتأكدنا من كده - ران فعلي رجّع HTTP 403 على كل "
                "حاجة تقريبًا). عشان كدا Fetcher Mode اتحول لـ 'Stealth Browser' والـ 'Delay between "
                "requests' اتحط على 2 ثانية على الأقل - ده هيخلي الرن أبطأ لكن الفرصة إنه ينجح أكبر "
                "بكتير. حتى كده، مفيش ضمان 100% - يلب دايمًا بيحاول يمنع أي سكرابينج.\n\n"
                "دوس Start Scraping على طول.",
            )
            return
        self.urls_input.setPlainText("\n".join(self.YELP_REAL_DIRECTORY_SEARCH_LINKS))
        self.container_selector_input.setText(YELP_CONTAINER["selector"])
        idx = self.container_type_combo.findText(YELP_CONTAINER["type"])
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)
        self._active_detail_config = YELP_DETAIL_CONFIG
        self._active_source_profiles = None  # single source - no per-URL resolution needed
        self.auto_qualify_chk.setChecked(True)
        self._apply_yelp_anti_block_settings()
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        QMessageBox.information(
            self, "Yelp Search Links",
            "دول 7 لينكات حقيقية من yelp.com لـ 7 من النيتشات بتاعتك، بنفس المدن اللي في زرار "
            "yellowpages فوق - ده اختبار سريع لمدينة واحدة لكل نيتش، مش تغطية الـ100 مدينة.\n\n"
            "عشان تغطية كاملة لنيتش واحد بس (كل الـ100 مدينة)، اختار النيتش من Quick Start فوق الأول "
            "ثم دوس الزرار ده تاني.\n\n"
            "الـ 'Repeat over' وسلكتور اسم الشركة اتحطوا أوتوماتيك في تاب Custom Selector. رقم "
            "التليفون مش موجود في صفحة نتايج البحث نفسها في يلب - عشان كدا LOGY هيعمل فتش تاني لكل "
            "بيزنس من صفحته الخاصة في يلب عشان يجيب رقم التليفون (السكرابينج هياخد وقت أطول شوية عشان "
            "كدا). auto-qualify اتفعّل تلقائي.\n\n"
            "⚠️ Fetcher Mode اتحول لـ 'Stealth Browser' والـ delay بين الطلبات اتحط 2 ثانية على الأقل - "
            "يلب بيحظر بسهولة على FAST/HTTP، ده بيقلل احتمال الحظر بس مش بيضمنه 100%.\n\n"
            "دوس Start Scraping على طول.",
        )

    def _load_all_sources_directory_links(self):
        """'كل اللينكات الممكنة في وقت واحد مش يمشي عليها واحد واحد' -
        put BOTH yellowpages.com AND yelp.com links in the SAME urls_input
        box at once and run them as one job, instead of the user loading
        one source, running it, exporting, then loading the other source
        and merging two CSVs by hand. Sets self._active_source_profiles so
        job_manager resolves each URL's own container/fields/detail_config
        by domain at fetch time (see SOURCE_PROFILES /
        ScrapeJobWorker._resolve_source()) - the Custom Selector box below
        is only ever used as a fallback here, since every URL this method
        loads matches one of the two known domains.

        If a niche is already selected in Quick Start above, this instead
        delegates to _apply_all_sources_niche() for THAT ONE niche's full
        top-100-city, both-sources list - see
        _current_niche_name_from_combo()'s docstring. That's the fix for
        "100 مدينة يعني الاقي 200 لينك في الكونتينر انما ال 14 لينك دول
        اعمل بيهم ايه؟": the 14-link list below is a fixed 7-niche demo
        (1 city each), never meant to BE the 100-city coverage."""
        niche_name = self._current_niche_name_from_combo()
        if niche_name:
            self._apply_all_sources_niche(niche_name)
            return

        combined = self.REAL_DIRECTORY_SEARCH_LINKS + self.YELP_REAL_DIRECTORY_SEARCH_LINKS
        self.urls_input.setPlainText("\n".join(combined))

        source_profiles = self._get_source_profiles()
        yp_profile = next(p for p in source_profiles if p["name"] == "yellowpages")
        yelp_profile = next(p for p in source_profiles if p["name"] == "yelp")
        thumbtack_profile = next((p for p in source_profiles if p["name"] == "thumbtack"), None)
        combined_fields = list(yp_profile["fields"])
        existing_names = {f.name for f in combined_fields}
        for extra_profile in (yelp_profile, thumbtack_profile):
            if not extra_profile:
                continue
            for f in extra_profile["fields"]:
                if f.name not in existing_names:
                    combined_fields.append(f)
                    existing_names.add(f.name)
        self.field_builder.load_fields(combined_fields)
        self.container_selector_input.setText(yp_profile["container"]["selector"])
        idx = self.container_type_combo.findText(yp_profile["container"].get("type", "css"))
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)

        self._active_detail_config = None  # handled per-URL via source_profiles instead
        self._active_source_profiles = source_profiles
        self.auto_qualify_chk.setChecked(True)
        self._apply_yelp_anti_block_settings()  # this run includes yelp.com URLs too
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        QMessageBox.information(
            self, "All Sources (combined)",
            f"اتحطت {len(combined)} رابط في نفس القائمة - {len(self.REAL_DIRECTORY_SEARCH_LINKS)} من "
            f"yellowpages.com و{len(self.YELP_REAL_DIRECTORY_SEARCH_LINKS)} من yelp.com، لـ 7 نيتشات "
            "بتاعتك (مدينة واحدة لكل نيتش - اختبار سريع، مش تغطية الـ100 مدينة).\n\n"
            "عشان تغطية كاملة لنيتش واحد بس (كل الـ100 مدينة، المصدرين مع بعض)، اختار النيتش من Quick "
            "Start فوق الأول (أي واحد من التلاتة) ثم دوس الزرار ده تاني - هيتحط لينكات النيتش دا بس على "
            "الـ100 مدينة تلقائي.\n\n"
            "كل رابط هياخد السلكتور الصح بتاعه أوتوماتيك حسب مصدره وقت السحب - مش هتحتاج تشغل كل "
            "مصدر لوحده ولا تدمج النتايج بنفسك. auto-qualify اتفعّل تلقائي.\n\n"
            "دوس Start Scraping على طول.",
        )

    # houzz.com - a real, large US directory (1,499+ pros just for "Kitchen
    # Remodelers" alone), confirmed live by browsing it directly. Deliberately
    # NOT wired into Custom Selector or SOURCE_PROFILES like yellowpages/
    # yelp: Houzz is built with fully randomized CSS-in-JS class names that
    # regenerate on every deploy (e.g. "sc-mwxddt-0 eMaGkh" - no stable
    # "businessName"-style prefix the way Yelp has), and card layouts vary
    # per listing (sponsored/video/plain), so there is no selector that
    # would keep working past the next Houzz release - writing one anyway
    # would be exactly the "looks like it works, silently breaks" behavior
    # the project explicitly forbids. AI Auto-Extract sidesteps this
    # entirely (reads visible page text, no selector needed) but only
    # returns ONE record per fetched page - see job_manager._extract_with_ai()
    # - so these are SEARCH-RESULTS pages used purely as a link-discovery
    # step (Follow Links, depth 1, restricted to "*-pf~*" URLs - Houzz's
    # own per-professional profile page pattern, confirmed live) that feed
    # into the individual profile pages, which DO extract cleanly one-per-
    # page (confirmed live: phone/address/rating are all in the profile's
    # visible text). The search-results page itself also gets "extracted"
    # as page 1 of the crawl and will likely produce one messy/incomplete
    # row (it has ~15 businesses on one page, not one) - a known, disclosed
    # limitation, not a hidden one.
    #
    # Also unlike yellowpages/Yelp, the city segment in a Houzz search URL
    # (e.g. "austin-tx-us") was confirmed live to NOT filter results - Houzz
    # silently redirects to the same nationwide "Near USA" list regardless
    # of city, and only the first ~15-page result set is covered here (no
    # confirmed pagination parameter). So this is nationwide-only, first-
    # page-only coverage per category, not a city-by-city generator like
    # generate_niche_urls() - stated plainly in the dialog below rather
    # than implied to be equivalent.
    #
    # Only 5 of the user's 15 ICP niches have a clean matching Houzz
    # category at all (Houzz is home-renovation/design focused - it has no
    # solar, foundation repair, waterproofing, or excavation category, and
    # none of the 5 non-home-service niches like attorneys or dental).
    HOUZZ_SEARCH_LINKS = [
        "https://www.houzz.com/professionals/pools-and-spas/probr0-bo~t_11795",              # Pool Builders
        "https://www.houzz.com/professionals/kitchen-remodelers/probr0-bo~t_34334",           # Kitchen Remodeling
        "https://www.houzz.com/professionals/kitchen-and-bath-remodelers/probr0-bo~t_11825",  # Bathroom Remodeling
        "https://www.houzz.com/professionals/painters/probr0-bo~t_27105",                     # Commercial Painters (closest match; Houzz painters skew residential)
        "https://www.houzz.com/professionals/house-cleaners/probr0-bo~t_27205",               # Commercial Cleaning (closest match; Houzz cleaners skew residential)
    ]

    def _load_houzz_directory_links(self):
        self.urls_input.setPlainText("\n".join(self.HOUZZ_SEARCH_LINKS))

        self.auto_qualify_chk.setChecked(True)
        self.ai_enabled_chk.setChecked(True)
        self.ai_fields_input.setText("business_name, phone, address, website, rating")
        self.extraction_tabs.setCurrentIndex(self.TAB_AI)

        # Follow Links is what turns each search-results page into ~15
        # individual profile-page fetches (see the module comment above) -
        # restricted to Houzz's own profile-URL pattern so LOGY doesn't
        # wander into /magazine/, /ideabooks/, login, etc.
        self.same_domain_chk.setChecked(True)
        self.follow_links_chk.setChecked(True)
        self.max_depth_spin.setValue(1)
        self.include_patterns_input.setText("*-pf~*")
        self.max_pages_spin.setValue(max(150, self.max_pages_spin.value()))

        # Not applicable in AI mode - clear any Custom Selector state a
        # previous button click may have set, so it can't leak in.
        self._active_detail_config = None
        self._active_source_profiles = None

        QMessageBox.information(
            self, "Houzz Search Links",
            f"اتحطت {len(self.HOUZZ_SEARCH_LINKS)} روابط بحث حقيقية من houzz.com، بس لـ 5 نيتشات بس "
            "من الـ 15 بتاعتك (Houzz دليل تصميم وتجديد منازل - معندوش تصنيف لسولار أو إصلاح أساسات أو "
            "عزل مائي أو حفر، ولا للنيتشات المهنية زي المحامين والأطباء).\n\n"
            "Houzz مبني بطريقة الكلاسات فيها بتتغير عشوائيًا كل ما الموقع يتحدث - فمفيش Custom Selector "
            "ثابت ممكن يفضل شغال. عشان كدا الزرار ده حوّل الشاشة لوضع 'AI Auto-Extract' ومفعّل 'Follow "
            "Links' - LOGY هيسحب صفحة النتايج، يلاقي فيها روابط بروفايلات الشركات، ويدخل كل بروفايل "
            "لوحده ويستخرج بياناته بالذكاء الاصطناعي (رقم التليفون والعنوان بيظهروا في نص الصفحة "
            "بشكل مباشر - اتأكدت من كده على صفحة حقيقية).\n\n"
            "⚠️ لازم يكون عندك مفتاح API لـ Anthropic أو OpenAI محفوظ في شاشة API Keys الأول، وإلا LOGY "
            "هيرفض يبدأ ويقولك. كل صفحة بروفايل بتتفتح هتكلفك استدعاء API حقيقي (مش مجاني زي yellowpages "
            "و yelp).\n\n"
            "حدود صريحة: دول أول صفحة نتايج بس لكل تصنيف (~15 شركة)، ومفيش فلترة حقيقية بالمدينة - "
            "Houzz بيرجع نفس القائمة القومية مهما غيرت المدينة في الرابط (اتأكدت من كده بنفسي)."
        )

    # ------------------------------------------------------------------
    # STEP 2: DATA TO EXTRACT
    # ------------------------------------------------------------------
    # Tab order in self.extraction_tabs - named so jump-to-tab calls below
    # don't silently break if a tab gets added/reordered (this exact bug
    # happened once already when AI Auto-Extract was inserted at index 0).
    TAB_AI, TAB_SMART, TAB_CUSTOM, TAB_SCHEMA = 0, 1, 2, 3

    def _build_extraction_section(self) -> QWidget:
        w, layout = card("Data to Extract")

        self.extraction_tabs = QTabWidget()

        # -- AI Auto-Extract (no selectors at all) --
        ai_tab = QWidget()
        ai_layout = QVBoxLayout(ai_tab)
        self.ai_enabled_chk = QCheckBox("Use AI Auto-Extract for this scrape (skips Custom Selector entirely)")
        ai_layout.addWidget(self.ai_enabled_chk)

        ai_provider_row = QHBoxLayout()
        ai_provider_row.addWidget(QLabel("Provider:"))
        self.ai_provider_combo = QComboBox()
        self.ai_provider_combo.addItem("Anthropic (Claude)", "anthropic")
        self.ai_provider_combo.addItem("OpenAI", "openai")
        ai_provider_row.addWidget(self.ai_provider_combo)
        ai_provider_row.addStretch(1)
        ai_layout.addLayout(ai_provider_row)

        self.ai_fields_input = QLineEdit(", ".join(DEFAULT_AI_FIELDS))
        self.ai_fields_input.setPlaceholderText("owner_name, email, phone, owner_linkedin_if_published")
        ai_layout.addWidget(QLabel("Fields to extract (comma-separated):"))
        ai_layout.addWidget(self.ai_fields_input)

        ai_note = QLabel(
            "No CSS/XPath needed - LOGY reads the page's visible text and asks the AI model to "
            "fill in these fields, returning null for anything not actually present (it's told "
            "never to guess or invent a value). One record per page, best for a business's own "
            "site or a directory profile page.\n\n"
            "Requires an API key saved under the exact name 'anthropic' or 'openai' on the API Keys "
            "screen. LOGY does NOT search LinkedIn itself for a person's profile (blocked by "
            "LinkedIn's own anti-bot measures and Terms of Service, and that crosses from scraping "
            "a business's public info into people-search on an individual, which carries real "
            "privacy-law exposure) - if a business's own page happens to publish an owner's LinkedIn "
            "link, this mode will pick it up like any other field; it just won't go looking for one."
        )
        ai_note.setWordWrap(True)
        ai_note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        ai_layout.addWidget(ai_note)
        ai_layout.addStretch(1)
        self.extraction_tabs.addTab(ai_tab, "AI Auto-Extract (no selectors)")

        # -- Smart Extraction --
        smart_tab = QWidget()
        smart_layout = QVBoxLayout(smart_tab)
        self.smart_description = QPlainTextEdit()
        self.smart_description.setPlaceholderText(
            "I need company name, website, email, phone number and address."
        )
        self.smart_description.setFixedHeight(70)
        smart_generate_btn = QPushButton("Generate Fields")
        smart_generate_btn.clicked.connect(self._generate_smart_fields)
        smart_note = QLabel(
            "Smart Extraction uses a keyword matcher to draft fields (no external AI call, "
            "no API key required). Selectors are left blank - fill them in with the Selector "
            "Assistant below or edit them directly in the Field Builder."
        )
        smart_note.setWordWrap(True)
        smart_note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        smart_layout.addWidget(self.smart_description)
        smart_layout.addWidget(smart_generate_btn)
        smart_layout.addWidget(smart_note)
        self.extraction_tabs.addTab(smart_tab, "Smart Extraction")

        # -- Custom Selector (Field Builder) --
        selector_tab = QWidget()
        selector_layout = QVBoxLayout(selector_tab)
        self.field_builder = FieldBuilder()
        selector_layout.addWidget(self.field_builder)

        container_row = QHBoxLayout()
        self.container_selector_input = QLineEdit()
        self.container_selector_input.setPlaceholderText("Repeat over (optional, e.g. .product-card) - leave empty for single-record pages")
        self.container_type_combo = QComboBox()
        self.container_type_combo.addItems(["css", "xpath"])
        container_row.addWidget(QLabel("Repeat over:"))
        container_row.addWidget(self.container_selector_input, 1)
        container_row.addWidget(self.container_type_combo)
        selector_layout.addLayout(container_row)
        self.extraction_tabs.addTab(selector_tab, "Custom Selector")

        # -- JSON Schema --
        schema_tab = QWidget()
        schema_layout = QVBoxLayout(schema_tab)
        self.schema_input = QPlainTextEdit()
        self.schema_input.setPlaceholderText(
            '{\n  "company_name": "string",\n  "email": "string",\n  "phone": "string",\n  "website": "string"\n}'
        )
        validate_schema_btn = QPushButton("Validate Schema")
        self.schema_status_label = QLabel("")
        validate_schema_btn.clicked.connect(self._validate_schema)
        schema_layout.addWidget(self.schema_input)
        schema_layout.addWidget(validate_schema_btn)
        schema_layout.addWidget(self.schema_status_label)
        self.extraction_tabs.addTab(schema_tab, "JSON Schema")

        layout.addWidget(self.extraction_tabs)
        return w

    def _generate_smart_fields(self):
        fields = generate_fields(self.smart_description.toPlainText())
        if not fields:
            QMessageBox.information(self, "Smart Extraction", "لم يتم التعرف على أي حقول من الوصف. جرّب وصف أوضح.")
            return
        self.field_builder.load_fields(fields)
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)  # jump to Custom Selector so the user fills in selectors

    def _validate_schema(self):
        ok, err, parsed = validate_json_schema(self.schema_input.toPlainText())
        if ok:
            self.schema_status_label.setText(f"✓ Schema صالح - {len(parsed)} حقل")
            self.schema_status_label.setStyleSheet("color: #22C55E;")
            fields = [ExtractionField(name=k, selector="") for k in parsed.keys()]
            self.field_builder.load_fields(fields)
        else:
            self.schema_status_label.setText(f"✗ {err}")
            self.schema_status_label.setStyleSheet("color: #EF4444;")

    # ------------------------------------------------------------------
    # STEP 3: SCRAPING OPTIONS
    # ------------------------------------------------------------------
    def _build_options_section(self) -> QWidget:
        w, layout = card("Scraping Options")

        form = QFormLayout()
        self.fetcher_combo = QComboBox()
        self.fetcher_combo.addItem("Fast / HTTP", FetcherMode.FAST_HTTP)
        self.fetcher_combo.addItem("Dynamic Browser", FetcherMode.DYNAMIC_BROWSER)
        self.fetcher_combo.addItem("Stealth Browser (anti-bot)", FetcherMode.STEALTH_BROWSER)
        form.addRow("Fetcher mode", self.fetcher_combo)
        layout.addLayout(form)

        # "هيستوري لليدز اللي طلعت مسبقا متتكررش كل ما نجينيريت ليدز" - skip
        # a lead this run extracts if ANY earlier job already produced it
        # (matched by email/phone/website/name+company - see
        # app/core/engine/dedupe.py). Checked against the History screen's
        # "Leads History" tab, which is exactly this same lead_history table.
        self.skip_duplicates_chk = QCheckBox("Skip leads already generated before (cross-job de-duplication)")
        self.skip_duplicates_chk.setChecked(True)
        self.skip_duplicates_chk.setToolTip(
            "Before saving a newly-extracted lead, LOGY checks it against every lead ANY previous "
            "job has ever produced (matched by email, then phone, then website, then name+company). "
            "A match is skipped instead of re-saved, so re-running the same niche/search later only "
            "brings back new leads. Uncheck to allow the same lead to be generated again. See the "
            "'Leads History' tab on the History screen, or 'Clear Leads History' there to reset it."
        )
        layout.addWidget(self.skip_duplicates_chk)

        toolbox = QToolBox()
        advanced = QWidget()
        adv_form = QFormLayout(advanced)

        self.headless_chk = QCheckBox("Headless")
        self.headless_chk.setChecked(True)
        self.network_idle_chk = QCheckBox("Wait for network idle")
        self.disable_resources_chk = QCheckBox("Disable images/CSS/fonts (faster)")
        self.solve_cloudflare_chk = QCheckBox("Auto-solve Cloudflare (Stealth mode only)")

        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 64)
        self.concurrency_spin.setValue(4)
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 60000)
        self.delay_spin.setSuffix(" ms")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(15)  # lower default so a dead/blocked target fails fast, not slow
        self.timeout_spin.setSuffix(" s")
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(1)

        self.headers_input = QPlainTextEdit()
        self.headers_input.setPlaceholderText("Header-Name: value  (one per line)")
        self.headers_input.setFixedHeight(60)
        self.cookies_input = QPlainTextEdit()
        self.cookies_input.setPlaceholderText("cookie_name=value  (one per line)")
        self.cookies_input.setFixedHeight(60)

        adv_form.addRow(self.headless_chk)
        adv_form.addRow(self.network_idle_chk)
        adv_form.addRow(self.disable_resources_chk)
        adv_form.addRow(self.solve_cloudflare_chk)
        adv_form.addRow("Concurrency", self.concurrency_spin)
        adv_form.addRow("Delay between requests", self.delay_spin)
        adv_form.addRow("Timeout", self.timeout_spin)
        adv_form.addRow("Retry count", self.retries_spin)
        adv_form.addRow("Request headers", self.headers_input)
        adv_form.addRow("Cookies", self.cookies_input)

        toolbox.addItem(advanced, "Advanced Options")
        layout.addWidget(toolbox)
        return w

    # ------------------------------------------------------------------
    # PROXY
    # ------------------------------------------------------------------
    def _build_proxy_section(self) -> QWidget:
        w, layout = card("Proxy")
        self.proxy_mode_combo = QComboBox()
        self.proxy_mode_combo.addItems(["No proxy", "Single proxy", "Proxy list", "Rotating"])
        self.proxy_list_input = QPlainTextEdit()
        self.proxy_list_input.setPlaceholderText("http://user:pass@host:port  (one per line)")
        self.proxy_list_input.setFixedHeight(70)
        layout.addWidget(self.proxy_mode_combo)
        layout.addWidget(self.proxy_list_input)
        note = QLabel("Proxy credentials are encrypted at rest and never written to logs or exports.")
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)
        return w

    # ------------------------------------------------------------------
    # LIVE RUN PANEL
    # ------------------------------------------------------------------
    def _build_run_panel(self) -> QWidget:
        w, layout = card("")

        status_row = QHBoxLayout()
        self.status_label = QLabel("IDLE")
        self.status_label.setObjectName("statusRunning")
        self.progress_bar = QProgressBar()
        self.pages_label = QLabel("Pages: 0 / 0")
        self.records_label = QLabel("Records: 0")
        self.success_label = QLabel("Success: 0")
        self.failed_label = QLabel("Failed: 0")
        self.elapsed_label = QLabel("Elapsed: 00:00:00")
        for lbl in (self.status_label, self.pages_label, self.records_label,
                    self.success_label, self.failed_label, self.elapsed_label):
            status_row.addWidget(lbl)
        status_row.addStretch(1)
        layout.addLayout(status_row)
        layout.addWidget(self.progress_bar)

        body_split = QSplitter(Qt.Orientation.Horizontal)

        self.log_panel = LogPanel()
        body_split.addWidget(self.log_panel)

        results_wrap = QWidget()
        results_layout = QVBoxLayout(results_wrap)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_header = QHBoxLayout()
        self.results_count_label = QLabel("Results Preview - 0 rows")
        export_btn = QPushButton("⇩ Export")
        export_btn.clicked.connect(self._export_results)
        results_header.addWidget(self.results_count_label)
        results_header.addStretch(1)
        results_header.addWidget(export_btn)
        results_layout.addLayout(results_header)

        self.results_view = QTableView()
        self.results_view.setAlternatingRowColors(True)
        self.results_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_view.horizontalHeader().setStretchLastSection(True)
        results_layout.addWidget(self.results_view)
        body_split.addWidget(results_wrap)
        body_split.setStretchFactor(0, 1)
        body_split.setStretchFactor(1, 2)

        layout.addWidget(body_split, 1)
        return w

    # ------------------------------------------------------------------
    # collecting config from the form
    # ------------------------------------------------------------------
    def _collect_target(self) -> TargetConfig | None:
        valid, invalid = parse_url_list(self.urls_input.toPlainText())
        if not valid:
            QMessageBox.warning(self, "Target", "لازم تدخل رابط واحد صحيح على الأقل.")
            return None
        return TargetConfig(
            start_urls=valid,
            same_domain_only=self.same_domain_chk.isChecked(),
            follow_links=self.follow_links_chk.isChecked(),
            max_pages=self.max_pages_spin.value(),
            max_depth=self.max_depth_spin.value(),
            respect_robots_txt=self.robots_chk.isChecked(),
            include_patterns=[p.strip() for p in self.include_patterns_input.text().split(",") if p.strip()],
            exclude_patterns=[p.strip() for p in self.exclude_patterns_input.text().split(",") if p.strip()],
        )

    def _collect_fields(self) -> list[ExtractionField] | None:
        if self.ai_enabled_chk.isChecked():
            return []  # AI Auto-Extract doesn't use CSS/XPath fields at all
        fields = self.field_builder.get_fields()
        fields = [f for f in fields if f.name and f.selector]
        if not fields:
            QMessageBox.warning(
                self, "Data to Extract",
                "لازم يكون فيه حقل واحد على الأقل بسلكتور فعلي في تبويب Custom Selector.\n"
                "لو استخدمت Smart Extraction أو JSON Schema، اضبط السلكتورات هناك الأول.\n"
                "أو فعّل AI Auto-Extract لو عايز تشتغل من غير سلكتورات خالص.",
            )
            return None
        return fields

    def _effective_ai_provider(self) -> str:
        """Which provider id to actually use: the AI Auto-Extract tab's
        provider dropdown if IT has a saved key, otherwise whichever
        supported provider DOES have one saved (if any).

        Why this matters: the dropdown always defaults to "Anthropic
        (Claude)" (it's added first) regardless of which provider the
        user actually saved a key for on the API Keys screen. "Look up
        owner contact info" lives outside that tab entirely, so a user
        who saved only an OpenAI key and never opened the AI Auto-Extract
        tab to switch the dropdown would otherwise hit a confusing "no
        API key found" - even though they do have one saved, just for the
        provider that isn't currently selected. This auto-picks whichever
        one actually has a key rather than failing on a UI default the
        user never touched; it only ever falls back like this when the
        selected provider has NO key at all, never overriding a real
        choice between two configured keys."""
        selected = self.ai_provider_combo.currentData()
        keys = self.db.get_setting("api_keys", {})
        if keys.get(selected):
            return selected
        for other_id in ("anthropic", "openai"):
            if other_id != selected and keys.get(other_id):
                return other_id
        return selected

    def _collect_ai_extraction(self) -> AIExtractionConfig | None:
        if not self.ai_enabled_chk.isChecked():
            # Still read the provider even with AI Auto-Extract itself off -
            # "Look up owner contact info" (owner_lookup_enabled below) is a
            # SEPARATE feature that also needs a provider/API key but works
            # alongside Custom Selector, not instead of it - it shouldn't
            # silently ignore whatever's picked in the AI Auto-Extract tab's
            # provider dropdown just because that tab itself isn't active.
            return AIExtractionConfig(enabled=False, provider=self._effective_ai_provider())
        field_names = [f.strip() for f in self.ai_fields_input.text().split(",") if f.strip()]
        if not field_names:
            QMessageBox.warning(self, "AI Auto-Extract", "لازم تحدد حقل واحد على الأقل تحت AI Auto-Extract.")
            return None
        return AIExtractionConfig(
            enabled=True,
            field_names=field_names,
            provider=self._effective_ai_provider(),
        )

    def _collect_options(self) -> ScrapeOptions:
        headers = {}
        for line in self.headers_input.toPlainText().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip()] = v.strip()
        cookies = {}
        for line in self.cookies_input.toPlainText().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cookies[k.strip()] = v.strip()

        proxy_mode_map = {0: "none", 1: "single", 2: "list", 3: "rotating"}
        proxy_mode = proxy_mode_map[self.proxy_mode_combo.currentIndex()]
        proxies = [p.strip() for p in self.proxy_list_input.toPlainText().splitlines() if p.strip()]

        return ScrapeOptions(
            fetcher_mode=self.fetcher_combo.currentData(),
            headless=self.headless_chk.isChecked(),
            concurrency=self.concurrency_spin.value(),
            delay_ms=self.delay_spin.value(),
            timeout_s=self.timeout_spin.value(),
            retries=self.retries_spin.value(),
            solve_cloudflare=self.solve_cloudflare_chk.isChecked(),
            network_idle=self.network_idle_chk.isChecked(),
            disable_resources=self.disable_resources_chk.isChecked(),
            headers=headers,
            cookies=cookies,
            proxy=ProxyConfig(mode=proxy_mode, proxies=proxies),
            auto_qualify_leads=self.auto_qualify_chk.isChecked(),
            ai_extraction=self._collect_ai_extraction() or AIExtractionConfig(enabled=False),
            owner_lookup_enabled=self.owner_lookup_chk.isChecked(),
            skip_duplicate_leads=self.skip_duplicates_chk.isChecked(),
        )

    def _collect_container(self) -> dict | None:
        sel = self.container_selector_input.text().strip()
        if not sel:
            return None
        return {"selector": sel, "type": self.container_type_combo.currentText()}

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def _save_project(self):
        target = self._collect_target()
        fields = self.field_builder.get_fields()
        if target is None:
            return
        options = self._collect_options()
        config = {
            "target": target.__dict__,
            "fields": [f.to_dict() for f in fields],
            "options": self._options_to_dict(options),
            "container": self._collect_container(),
        }
        name = f"Project {self.db.list_projects().__len__() + 1}"
        project_id = self.db.create_project(name, config)
        QMessageBox.information(self, "Saved", f"تم حفظ المشروع باسم: {name}")

    def _options_to_dict(self, options: ScrapeOptions) -> dict:
        d = dict(options.__dict__)
        d["fetcher_mode"] = options.fetcher_mode.value
        d["proxy"] = {"mode": options.proxy.mode, "proxies": options.proxy.proxies}
        d["ai_extraction"] = {
            "enabled": options.ai_extraction.enabled,
            "field_names": options.ai_extraction.field_names,
            "provider": options.ai_extraction.provider,
        }
        return d

    def _resolve_ai_api_key_present(self, provider: str) -> bool:
        keys = self.db.get_setting("api_keys", {})
        return bool(keys.get(provider))

    def _start_scraping(self):
        if not engine.SCRAPLING_AVAILABLE:
            QMessageBox.critical(
                self, "Engine not ready",
                f"Scrapling مش مثبت في البيئة دي.\n\n{engine.SCRAPLING_IMPORT_ERROR}\n\n"
                "شغّل: pip install scrapling && scrapling install",
            )
            return

        target = self._collect_target()
        if target is None:
            return
        fields = self._collect_fields()
        if fields is None:
            return
        if self._collect_ai_extraction() is None:  # validates + shows a warning dialog on failure
            return
        options = self._collect_options()
        if self.ai_enabled_chk.isChecked() and not self._resolve_ai_api_key_present(options.ai_extraction.provider):
            QMessageBox.warning(
                self, "AI Auto-Extract",
                f"مفيش مفتاح API متسجل باسم '{options.ai_extraction.provider}' في شاشة API Keys.\n"
                "ضيفه الأول، أو بدّل لـ Custom Selector.",
            )
            return
        if self.owner_lookup_chk.isChecked() and not self._resolve_ai_api_key_present(options.ai_extraction.provider):
            QMessageBox.warning(
                self, "Owner Lookup",
                f"'Look up owner contact info' محتاج مفتاح API متسجل باسم '{options.ai_extraction.provider}' "
                "في شاشة API Keys الأول (اختار المزوّد من تاب AI Auto-Extract لو عايز تغيّره).\n"
                "ضيفه الأول، أو بطّل الخانة دي.",
            )
            return
        container = self._collect_container()

        self.run_panel.setVisible(True)
        self.log_panel.clear()
        self.status_label.setText("RUNNING")
        self.start_btn.setVisible(False)
        self.pause_btn.setVisible(True)
        self.stop_btn.setVisible(True)
        self.pause_btn.setText("⏸ Pause")

        # prepare_job() builds the worker/thread but does NOT start it -
        # every signal below gets connected first, THEN
        # start_prepared_job() actually starts the thread. Doing it the
        # other way around (start, then connect) is a race that can miss
        # the run's earliest log/progress/status emissions entirely - see
        # JobManager.prepare_job()'s docstring for the full explanation.
        job_id, worker = self.job_manager.prepare_job(
            None, target, fields, options, container, self._active_detail_config, self._active_source_profiles
        )
        self.current_job_id = job_id
        self._job_start_ts = __import__("time").time()

        worker.log.connect(self._on_log)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_result)
        worker.status_changed.connect(self._on_status_changed)
        worker.finished.connect(self._on_finished)

        self.results_model = ResultsTableModel(self.db, job_id)
        self.results_view.setModel(self.results_model)

        self._elapsed_timer = self.startTimer(1000)

        self.job_manager.start_prepared_job()

    def timerEvent(self, event):
        if hasattr(self, "_job_start_ts") and self.job_manager.is_running:
            import time
            elapsed = int(time.time() - self._job_start_ts)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.elapsed_label.setText(f"Elapsed: {h:02}:{m:02}:{s:02}")

    def _toggle_pause(self):
        currently_paused = self.pause_btn.text().startswith("▶")
        self.job_manager.pause(not currently_paused)
        self.pause_btn.setText("▶ Resume" if not currently_paused else "⏸ Pause")

    def _stop_scraping(self):
        self.job_manager.stop()

    def _on_log(self, level: str, message: str):
        self.log_panel.append_entry(level, message)

    def _on_progress(self, pages_done, pages_total, records_ok, records_failed):
        self.pages_label.setText(f"Pages: {pages_done} / {pages_total}")
        self.records_label.setText(f"Records: {records_ok + records_failed}")
        self.success_label.setText(f"Success: {records_ok}")
        self.failed_label.setText(f"Failed: {records_failed}")
        if pages_total:
            self.progress_bar.setValue(int(pages_done / pages_total * 100))

    def _on_result(self, record: dict):
        if self.results_model:
            self.results_model.append_live_result()
            self.results_count_label.setText(f"Results Preview - {self.results_model.total_count} rows")

    def _on_status_changed(self, status: str):
        self.status_label.setText(status.upper())
        obj_names = {
            JobStatus.RUNNING.value: "statusRunning",
            JobStatus.COMPLETED.value: "statusCompleted",
            JobStatus.FAILED.value: "statusFailed",
            JobStatus.PAUSED.value: "statusPaused",
            JobStatus.STOPPED.value: "statusFailed",
        }
        self.status_label.setObjectName(obj_names.get(status, "statusRunning"))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _on_finished(self, job_id: int):
        self.start_btn.setVisible(True)
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        if hasattr(self, "_elapsed_timer"):
            self.killTimer(self._elapsed_timer)

    def _export_results(self):
        if not self.current_job_id:
            QMessageBox.information(self, "Export", "مفيش نتائج لسه.")
            return
        fmt, ok = self._ask_export_format()
        if not ok:
            return
        # "odoo_xlsx"/"odoo_xls" map to REAL .xlsx/.xls files respectively
        # (see exporter.export_odoo_xlsx / export_odoo_xls) - only the
        # internal EXPORTERS key has the odoo_ prefix, the file extension
        # on disk must match the actual bytes written or Excel/Odoo may
        # reject or mis-parse it. "عايزه يطلع XLS مش XSLS" - Odoo's own
        # downloadable CRM Lead template is itself a "crm_lead 1.xls"
        # file, so that's the default Odoo option now instead of .xlsx.
        ODOO_EXTENSIONS = {"odoo_xlsx": "xlsx", "odoo_xls": "xls"}
        extension = ODOO_EXTENSIONS.get(fmt, fmt)
        default_name = f"crm_leads.{extension}" if fmt in ODOO_EXTENSIONS else f"results.{extension}"
        path, _ = QFileDialog.getSaveFileName(self, "Export results", default_name, f"*.{extension}")
        if not path:
            return
        # Both odoo_* formats' "Channel" column is a required field only
        # on THIS user's own Odoo instance (not part of Odoo's stock
        # crm.lead import template - see exporter.py's
        # DEFAULT_ODOO_CHANNEL_VALUE docstring) - its value comes from
        # Settings -> "Odoo Export" (db setting "odoo_channel_value"),
        # never guessed here.
        extra_kwargs = {}
        if fmt in ODOO_EXTENSIONS:
            extra_kwargs["channel_value"] = self.db.get_setting("odoo_channel_value", exporter.DEFAULT_ODOO_CHANNEL_VALUE)
        try:
            count = exporter.export(fmt, self.db.iter_all_results(self.current_job_id), path, **extra_kwargs)
            QMessageBox.information(self, "Export", f"تم تصدير {count} سجل إلى:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def _ask_export_format(self) -> tuple[str, bool]:
        from PySide6.QtWidgets import QInputDialog
        # "Odoo CRM Lead template (.xls)" listed FIRST/default - matches
        # Odoo's own downloadable template file, which is itself a
        # "crm_lead 1.xls" (see exporter.export_odoo_xls's docstring for
        # why this is a genuine legacy-format file, not just a renamed
        # .xlsx). The .xlsx variant is kept available for anyone whose
        # own Odoo import screen prefers .xlsx instead.
        labels = {
            "odoo_xls": "Odoo CRM Lead template (.xls)",
            "odoo_xlsx": "Odoo CRM Lead template (.xlsx)",
            "csv": "csv", "json": "json", "jsonl": "jsonl", "xlsx": "xlsx",
        }
        label, ok = QInputDialog.getItem(self, "Export format", "Choose a format:", list(labels.values()), 0, False)
        if not ok:
            return "", False
        reverse = {v: k for k, v in labels.items()}
        return reverse[label], True
