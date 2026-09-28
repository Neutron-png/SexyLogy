from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal, QSize, QRectF
from PySide6.QtGui import QFont, QColor, QPainter
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QLineEdit, QTabWidget, QComboBox, QSpinBox, QCheckBox, QFormLayout,
    QScrollArea, QMessageBox, QFileDialog, QProgressBar, QSplitter, QToolBox,
    QTableView, QAbstractItemView, QListWidget, QListWidgetItem,
    QStyledItemDelegate, QStyle, QFrame,
)

from app.core.models import (
    ExtractionField, TargetConfig, ScrapeOptions, ProxyConfig, FetcherMode, JobStatus, AIExtractionConfig,
)
from app.core.engine.nl_to_fields import generate_fields
from app.core.engine.ai_extractor import DEFAULT_FIELD_NAMES as DEFAULT_AI_FIELDS
from app.core.engine import fetch_engine as engine
from app.core.engine.builtin_templates import (
    generate_niche_urls_per_city,
    YELP_CONTAINER, YELP_DETAIL_CONFIG,
    ICP_NICHES, CITY_POOL,
    get_all_source_profiles,
)
from app.core.exports import exporter
from app.core.job_manager import JobManager
from app.core.storage.db import Database
from app.utils.validation import parse_url_list, validate_json_schema
from app.ui.dialogs.source_dialog import SourceDialog
from app.ui.dialogs.city_picker_dialog import CityPickerDialog
from app.ui.widgets.accordion import Accordion
from app.ui.widgets.buttons import PrimaryButton
from app.ui.widgets import icons
from app.ui.widgets.field_builder import FieldBuilder
from app.ui.widgets.log_panel import ActivityFeed
from app.ui.widgets.results_table import ResultsTableModel, QualityPillDelegate
from app.ui.widgets.switch import Switch, ThemeToggle


def card(title: str) -> tuple[QWidget, QVBoxLayout]:
    """Plain content container (no chrome) — the hero/accordion provide styling."""
    w = QWidget()
    layout = QVBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    if title:
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
    return w, layout


class SourceListDelegate(QStyledItemDelegate):
    """Concept-17 sources row: colored initial badge + name + right-aligned
    'Built-in · Ready' tag, instead of one flat text line."""

    BADGE_BG = {
        "y": "#2E6BFF", "r": "#FF4B4B", "b": "#0AA8A0", "m": None,
    }

    def paint(self, p: QPainter, opt: QStyleOptionViewItem, index):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from app.ui import theme as ui_theme
        dark = ui_theme.current() == "dark"
        tk = ui_theme.tokens()

        sel = opt.state & QStyle.StateFlag.State_Selected
        hover = opt.state & QStyle.StateFlag.State_MouseOver
        r = opt.rect
        if sel:
            p.fillRect(r, QColor(0, 0, 0, 0))  # selection tint painted by accent-soft below
            c = QColor(tk["PRIMARY"]); c.setAlphaF(0.16 if dark else 0.10)
            p.fillRect(r, c)
        elif hover:
            c = QColor(255, 255, 255); c.setAlphaF(0.05 if dark else 0.45)
            p.fillRect(r, c)

        name = index.data(Qt.ItemDataRole.UserRole + 1) or ""
        domain = index.data(Qt.ItemDataRole.UserRole + 2) or ""
        letter = index.data(Qt.ItemDataRole.UserRole + 3) or "?"
        badge_kind = index.data(Qt.ItemDataRole.UserRole + 4) or "m"
        tag = index.data(Qt.ItemDataRole.UserRole + 5) or ""

        # badge chip
        bs = 22
        bx, by = r.x() + 11, r.y() + (r.height() - bs) // 2
        bg_hex = self.BADGE_BG.get(badge_kind)
        if bg_hex:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(bg_hex))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 41 if dark else 46))
        p.drawEllipse(QRectF(bx, by, bs, bs))
        p.setPen(QColor("#FFFFFF") if bg_hex else QColor("#8FA3C0" if dark else "#5A6073"))
        f = p.font(); f.setPointSizeF(8.5); f.setBold(True); p.setFont(f)
        p.drawText(QRectF(bx, by, bs, bs), Qt.AlignmentFlag.AlignCenter, letter)

        # name + domain
        f = p.font(); f.setBold(False); f.setPointSizeF(9.5); p.setFont(f)
        p.setPen(QColor(tk["TEXT"]))
        p.drawText(QRectF(bx + bs + 9, r.y(), 240, r.height()), Qt.AlignmentFlag.AlignVCenter, name)
        fw = p.fontMetrics().horizontalAdvance(name)
        p.setPen(QColor(tk["MUTED"]))
        p.drawText(QRectF(bx + bs + 9 + fw + 8, r.y(), r.width(), r.height()),
                   Qt.AlignmentFlag.AlignVCenter, f"({domain})")

        # right tag
        if tag:
            p.setPen(QColor(tk["MUTED"]))
            f = p.font(); f.setPointSizeF(8.5); p.setFont(f)
            p.drawText(r.adjusted(0, 0, -12, 0), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, tag)
        p.restore()

    def sizeHint(self, opt, index):
        return QSize(opt.rect.width() if opt.rect.width() else 300, 30)


class NewScrapeScreen(QWidget):
    theme_changed = Signal(bool)
    open_logs = Signal()

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
        # restrict kol search links tani generated l medon el user ekhtarha (default: kol 100)
        # Defaults to the full CITY_POOL (every
        # box checked in the picker) - the previous, only, behavior.
        # Changed via _open_city_picker() below; read by _collect_cities().
        self._selected_cities: list[tuple[str, str]] = list(CITY_POOL)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ---- header: title + theme switch (prototype topbar) ----
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("New Campaign")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Set it up in under a minute — LOGY handles the technical part.")
        subtitle.setObjectName("pageSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)

        self.save_btn = QPushButton("Save Draft")
        self.start_btn = PrimaryButton("Start Campaign")
        self.pause_btn = QPushButton("Pause")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.pause_btn.setVisible(False)
        self.stop_btn.setVisible(False)
        for b in (self.save_btn, self.start_btn, self.pause_btn, self.stop_btn):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button_icons()
        self.theme_toggle = ThemeToggle()
        self.theme_toggle.transitionStarted.connect(self.theme_changed.emit)
        header.addWidget(self.theme_toggle)
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

        # hero: the whole setup in one visual sentence
        config_layout.addWidget(self._build_hero_section())

        # everything else: collapsed accordion rows
        self.acc = Accordion()
        self.acc.add_row(
            "sources", "sources", "Data sources", "where LOGY pulls leads from",
            self._sources_summary(), self._build_sources_section(), expanded=True,
        )
        self.acc.add_row(
            "links", "links", "Search links", "generated for you",
            "auto-filled per city", self._build_target_section(),
        )
        self.acc.add_row(
            "table", "table", "Lead details", "the columns you get",
            "Standard columns · + Quality", self._build_extraction_section(),
        )
        adv_content = QWidget()
        adv_layout = QVBoxLayout(adv_content)
        adv_layout.setContentsMargins(0, 0, 0, 0)
        adv_layout.setSpacing(14)
        self.options_section = self._build_options_section()
        self.proxy_section = self._build_proxy_section()
        self.intel_section = self._build_intelligence_section()
        adv_layout.addWidget(self.options_section)
        adv_layout.addWidget(self.proxy_section)
        adv_layout.addWidget(self.intel_section)
        self.acc.add_row(
            "sliders", "sliders", "Advanced", "safe defaults — change only if you need",
            "Protection: rotating · Pace: balanced", adv_content,
        )
        config_layout.addWidget(self.acc)

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

        # ---- footer hint strip (concept foot-hint) ----
        foot = QFrame()
        foot.setObjectName("footHint")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 7, 24, 7)
        fl.setSpacing(8)
        self.foot_dot = QLabel()
        self.foot_dot.setFixedSize(8, 8)
        self.foot_dot.setObjectName("footDotOk")
        fl.addWidget(self.foot_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        ft1 = QLabel("Protected connection")
        ft1.setObjectName("footHintStrong")
        fl.addWidget(ft1)
        ft2 = QLabel("· encrypted on your device")
        ft2.setObjectName("footHintText")
        fl.addWidget(ft2)
        fl.addStretch(1)
        ft3 = QLabel("Ctrl+↵ start · Ctrl+E export")
        ft3.setObjectName("footHintText")
        fl.addWidget(ft3)
        root.addWidget(foot)

    # ------------------------------------------------------------------
    # HERO: the whole setup in one visual sentence. Niche, cities and
    # reach are inline glass pills; the technical rest lives in the
    # accordion below (sources / links / details / advanced).
    # ------------------------------------------------------------------
    def refresh_button_icons(self):
        from app.ui import theme as ui_theme
        dark = ui_theme.current() == "dark"
        muted = "#8FA3C0" if dark else "#5A6073"
        danger = "#FF8B8B" if dark else "#DC2626"
        self.save_btn.setIcon(icons.icon("save", muted, 17))
        self.start_btn.setIcon(icons.icon("play", "#FFFFFF", 17))
        self.pause_btn.setIcon(icons.icon("pause", muted, 17))
        self.stop_btn.setIcon(icons.icon("stop", danger, 17))
        if hasattr(self, "export_btn"):
            self.export_btn.setIcon(icons.icon("export", muted, 17))

    def _build_hero_section(self) -> QWidget:
        w = QWidget()
        w.setObjectName("heroCard")
        layout = QVBoxLayout(w)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        kicker = QLabel("NEW CAMPAIGN")
        kicker.setObjectName("heroKicker")
        kf = kicker.font()
        kf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.6)
        kicker.setFont(kf)
        layout.addWidget(kicker)

        # line 1: Find [niche] in [cities]
        line1 = QHBoxLayout()
        line1.setSpacing(8)
        l1 = QLabel("Find")
        l1.setObjectName("heroText")
        line1.addWidget(l1)

        self.niche_combo = QComboBox()
        self.niche_combo.setObjectName("heroPill")
        self.niche_combo.addItem("Choose a niche...", None)
        for niche, fee, _term in ICP_NICHES:
            fee_tag = f"  ({fee})" if fee else ""
            self.niche_combo.addItem(f"{niche}{fee_tag}", niche)
        self.niche_combo.currentIndexChanged.connect(self._auto_apply_niche)
        self.niche_combo.setMaximumWidth(270)
        self.niche_combo.setMinimumHeight(34)
        line1.addWidget(self.niche_combo)

        l2 = QLabel("in")
        l2.setObjectName("heroText")
        line1.addWidget(l2)

        self.choose_cities_btn = QPushButton("All cities")
        self.choose_cities_btn.setObjectName("heroPill")
        self.choose_cities_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.choose_cities_btn.setMinimumHeight(34)
        self.choose_cities_btn.setMaximumWidth(240)
        self.choose_cities_btn.setToolTip(
            "Restricts every generated search URL (Quick Start and the Target section's "
            "'Load ... Search Links' buttons) to just the cities you check here, instead of "
            "always spreading across all top 100."
        )
        self.choose_cities_btn.clicked.connect(self._open_city_picker)
        line1.addWidget(self.choose_cities_btn)
        line1.addStretch(1)
        layout.addLayout(line1)

        # line 2: — search up to [reach] links per city, across all sources
        line2 = QHBoxLayout()
        line2.setSpacing(8)
        l3 = QLabel("— search up to")
        l3.setObjectName("heroText")
        line2.addWidget(l3)

        self.reach_combo = QComboBox()
        self.reach_combo.setObjectName("heroPill")
        for label, value in (("250", 250), ("500", 500), ("1,000", 1000), ("2,000", 2000)):
            self.reach_combo.addItem(label, value)
        self.reach_combo.setCurrentIndex(2)
        self.reach_combo.currentIndexChanged.connect(self._on_reach_changed)
        self.reach_combo.setMinimumHeight(34)
        self.reach_combo.setMaximumWidth(150)
        line2.addWidget(self.reach_combo)

        l4 = QLabel("links per city, across yellowpages, Yelp & Thumbtack combined.")
        l4.setObjectName("heroText")
        line2.addWidget(l4)
        line2.addStretch(1)
        layout.addLayout(line2)

        # hidden spin keeps the existing per-city budget logic untouched
        self.target_results_spin = QSpinBox()
        self.target_results_spin.setRange(1, 2001)
        self.target_results_spin.setSingleStep(50)
        self.target_results_spin.setValue(1000)
        self.target_results_spin.setVisible(False)
        layout.addWidget(self.target_results_spin)

        toggles_row = QHBoxLayout()
        toggles_row.setSpacing(26)
        self.auto_qualify_chk = Switch("Rate lead quality automatically")
        self.auto_qualify_chk.setToolTip(
            "For each result with a website field, LOGY fetches that site and flags it "
            "'no website' / 'weak site' / 'strong site' based on real page signals "
            "(HTTPS, mobile-friendliness, SEO basics). No selector knowledge needed."
        )
        self.auto_qualify_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        toggles_row.addWidget(self.auto_qualify_chk)
        self.owner_lookup_chk = Switch("Find owner contacts from company sites (AI)")
        self.owner_lookup_chk.setToolTip(
            "For each result with a website field, LOGY fetches that site and asks the AI model "
            "(same provider/key as AI Auto-Extract below) to fill in owner_name / owner_email / "
            "owner_phone / owner_linkedin_if_published from whatever that site actually publishes "
            "(e.g. an 'About Us' or 'Meet the Owner' page) - never invented, and never a LinkedIn "
            "search on LOGY's own initiative. Adds one extra fetch + API call per lead, so a large "
            "run will be slower and cost more API usage."
        )
        self.owner_lookup_chk.setCursor(Qt.CursorShape.PointingHandCursor)
        toggles_row.addWidget(self.owner_lookup_chk)
        toggles_row.addStretch(1)
        layout.addLayout(toggles_row)

        self.quick_start_status = QLabel("")
        self.quick_start_status.setStyleSheet("color: #2EE6A8; font-size: 12px;")
        self.quick_start_status.setWordWrap(True)
        layout.addWidget(self.quick_start_status)

        foot = QHBoxLayout()
        hint = QLabel("Quality checks on · nothing technical required")
        hint.setObjectName("pageSubtitle")
        foot.addWidget(hint)
        foot.addStretch(1)
        foot.addWidget(self.save_btn)
        foot.addWidget(self.start_btn)
        layout.addLayout(foot)

        self._update_cities_summary()
        return w

    def _auto_apply_niche(self):
        if self.niche_combo.currentData() is not None:
            self._apply_niche_template()
            self._apply_max_protection_settings()

    def _on_reach_changed(self, index: int):
        value = self.reach_combo.itemData(index)
        if value:
            self.target_results_spin.setValue(int(value))

    # ------------------------------------------------------------------
    # INTELLIGENCE (brain.py) - identity memory, pacing, cache, sitemap, PoW
    # ------------------------------------------------------------------
    def _build_intelligence_section(self) -> QWidget:
        w, layout = card("")
        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Selection"))
        self.selection_combo = QComboBox()
        self.selection_combo.addItems([
            "Sticky + weighted (default)",
            "Weighted only (pure posterior)",
            "UCB1 - optimism (most exploratory)",
        ])
        sel_row.addWidget(self.selection_combo)
        sel_row.addStretch(1)
        layout.addLayout(sel_row)

        self.pacing_combo = QComboBox()
        self.pacing_combo.addItems([
            "Fixed delay (classic)",
            "Adaptive — learns each site's speed",
            "Human-like bursts (self-excited rhythm)",
        ])
        layout.addWidget(self.pacing_combo)

        self.identity_memory_chk = QCheckBox("Identity memory — per-identity reputation, cooldown, sticky per domain (persisted)")
        self.identity_memory_chk.setChecked(True)
        layout.addWidget(self.identity_memory_chk)

        self.response_cache_chk = QCheckBox("Response cache — ETag/304 skips pages seen in earlier runs")
        layout.addWidget(self.response_cache_chk)

        self.sitemap_chk = QCheckBox("Sitemap discovery — collect links from sitemap.xml instead of protected search pages")
        layout.addWidget(self.sitemap_chk)

        self.pow_chk = QCheckBox("Detect JS proof-of-work walls (experimental)")
        layout.addWidget(self.pow_chk)

        note = QLabel(
            "Identity memory keeps a reputation per identity (Beta posterior), avoids blocked ones for 5 minutes "
            "instead of discarding them, and sticks one identity per domain like a human would. Each identity "
            "has a stable fingerprint (UA + language + timing).\n"
            "AIMD: x0.9 on success, x2 on block — it finds the sweet spot per site. Human-burst: request gaps "
            "are self-excited (bursts + quiet) instead of evenly spaced machine-like gaps."
        )
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        note.setWordWrap(True)
        layout.addWidget(note)
        return w

    # ------------------------------------------------------------------
    # SOURCES - user ye2dar yezawed sources men gowa el app:
    # define a new scraping source (domain + container/fields selectors,
    # optionally a 2nd-fetch detail_config) from inside the app instead
    # of editing app/core/engine/builtin_templates.py by hand. Saved
    # sources are stored via Database.create_custom_source() and merged
    # with the built-ins (yellowpages, yelp, thumbtack) by
    # get_all_source_profiles() - see _get_source_profiles() above -
    # everywhere a multi-source ("All Sources") run is built.
    # ------------------------------------------------------------------
    def _sources_summary(self) -> str:
        try:
            profiles = self._get_source_profiles()
        except Exception:
            return "3 built-in ready"
        built = sum(1 for p in profiles if p.get("verified", True))
        custom = len(profiles) - built
        text = f"{built} built-in ready"
        if custom:
            text += f" + {custom} custom"
        return text

    def _build_sources_section(self) -> QWidget:
        w, layout = card("")
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

        self.sources_frame = QFrame()
        self.sources_frame.setObjectName("tablePanel")
        sf = QVBoxLayout(self.sources_frame)
        sf.setContentsMargins(8, 4, 8, 4)
        self.sources_list = QListWidget()
        self.sources_list.setFixedHeight(120)
        self.sources_list.setItemDelegate(SourceListDelegate(self.sources_list))
        self.sources_list.setIconSize(QSize(1, 1))
        sf.addWidget(self.sources_list)
        layout.addWidget(self.sources_frame)

        row = QHBoxLayout()
        add_btn = PrimaryButton("Add source")
        add_btn.setIcon(icons.icon("new", "#FFFFFF", 16))
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
        BADGE = {"yellowpages": ("Y", "y"), "yelp": ("Yp", "r"), "thumbtack": ("T", "b")}
        for profile in self._get_source_profiles():
            custom_row = next(
                (r for r in custom_rows if r["name"] == profile["name"] and r["domain"] == profile["domain"]),
                None,
            )
            if custom_row:
                badge = ("@", "m")
            else:
                badge = BADGE.get(profile["name"], (profile["name"][:1].upper(), "m"))
            kind = "Custom" if custom_row else "Built-in · Ready"
            unverified = "" if profile.get("verified", True) else "  ·  selectors not confirmed"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, custom_row["id"] if custom_row else None)
            item.setData(Qt.ItemDataRole.UserRole + 1, profile["name"])
            item.setData(Qt.ItemDataRole.UserRole + 2, profile["domain"])
            item.setData(Qt.ItemDataRole.UserRole + 3, badge[0])
            item.setData(Qt.ItemDataRole.UserRole + 4, badge[1])
            item.setData(Qt.ItemDataRole.UserRole + 5, kind + unverified)
            self.sources_list.addItem(item)
        self.acc.set_summary("sources", self._sources_summary())

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
            QMessageBox.information(self, "Edit Source", "Pick one of your own custom sources first (built-in sources can't be edited here).")
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
            QMessageBox.information(self, "Delete Source", "Pick one of your own custom sources first (built-in sources can't be deleted).")
            return
        reply = QMessageBox.question(self, "Delete Source", "Delete this source?")
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
        if n == total:
            self.choose_cities_btn.setText(f"All {total} cities")
        else:
            self.choose_cities_btn.setText(f"{n} of {total} cities")

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
        choose (see _apply_niche_template() above - kol niche bygeeb kol el masader automatic). The
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
        self._apply_max_protection_settings()  # this run includes yelp.com URLs too

        # budget l kol medina leha nafsaha - generate_niche_urls_per_city()
        # gives every selected city its OWN up-to-target_count budget
        # (see the spinbox's comment above / that function's docstring),
        # instead of generate_niche_urls_all_sources()' shared global
        # split which is what produced only ~10 URLs total regardless of
        # how many cities were selected.
        urls_per_city = self.target_results_spin.value()
        cities = self._collect_cities()
        start_urls = generate_niche_urls_per_city(niche_name, urls_per_city, cities=cities)
        if start_urls:
            self.urls_input.setPlainText("\n".join(start_urls))
            self.max_pages_spin.setValue(max(len(start_urls), self.max_pages_spin.value()))

        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        if start_urls:
            yp_count = sum(1 for u in start_urls if "yellowpages.com" in u)
            yelp_count = sum(1 for u in start_urls if "yelp.com" in u)
            thumbtack_count = len(start_urls) - yp_count - yelp_count
            city_count = len(cities) if cities else len(CITY_POOL)
            self.quick_start_status.setText(
                f"'{niche_name}' is ready — {len(start_urls)} search links across {city_count} cities (up to "
                f"{urls_per_city} per city: {yp_count} from yellowpages.com + "
                f"{yelp_count} from yelp.com + {thumbtack_count} from thumbtack.com) in the list below. "
                "Each link picks its own template automatically — no need to run sources one by one or merge "
                "results by hand. Note: none of the three returns 50 leads per link — the per-page max is "
                "30 on yellowpages.com and 10 on yelp/thumbtack — this number controls how deep LOGY goes "
                "per city, not leads per link. Thumbtack leads carry name + rating + profile link only "
                "(no phone/website — the site doesn't publish them publicly), and some niches have no real "
                "Thumbtack category so it may return zero leads, but the other two cover them. Start Campaign now."
            )
        else:
            self.quick_start_status.setText(
                f"Couldn't generate links for '{niche_name}' — try another niche or lower the reach."
            )

    # ------------------------------------------------------------------
    # STEP 1: TARGET
    # ------------------------------------------------------------------
    def _build_target_section(self) -> QWidget:
        w, layout = card("")

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
            "These are public practice sites built for scraper developers — not real lead sources.\n\n"
            "Use them to confirm LOGY works end to end (fetch, extract, export), then point it at "
            "your real target.",
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

    def _apply_max_protection_settings(self):
        """The 'best chance of actually getting leads' preset, applied as
        soon as a niche is picked (and whenever directory links load):

        1. Connection stays "Automatic" (fast HTTP) - the engine now
           routes per SOURCE instead of per JOB: yelp/thumbtack profiles
           declare STEALTH for themselves (real browser, WAF), while
           yellowpages pages keep the fast lane (2-4s instead of 5-27s).
           The per-page WAF rescue escalates on demand for anything else.
        2. A real pause between requests (>= 2000 ms) - hammering a source
           back-to-back is exactly what bot detection is built to catch
           (the original 500-page Yelp run that got 403 on ~everything).
        3. The intelligence layer fully on: human-like burst pacing,
           identity memory (persisted per-identity reputation) and UCB1
           selection - blocked identities get skipped instantly and the
           healthiest one is preferred next request. Burst gaps are
           capped relative to the user's delay so "human rhythm" can't
           silently become 30s-per-request.
        4. Cloudflare handling, 2 retries, 20s timeout, cross-job dedupe.

        Everything here is a FLOOR (max()/setChecked), never lowering a
        value the user set manually - and everything stays user-editable
        afterwards."""
        idx = self.fetcher_combo.findData(FetcherMode.FAST_HTTP)
        if idx >= 0:
            self.fetcher_combo.setCurrentIndex(idx)
        self.delay_spin.setValue(max(2000, self.delay_spin.value()))
        self.pacing_combo.setCurrentIndex(2)      # human-like bursts
        self.selection_combo.setCurrentIndex(0)   # sticky + weighted (UCB1 kept for power users)
        self.identity_memory_chk.setChecked(True)
        self.solve_cloudflare_chk.setChecked(True)
        self.retries_spin.setValue(max(2, self.retries_spin.value()))
        self.timeout_spin.setValue(max(20, self.timeout_spin.value()))
        self.skip_duplicates_chk.setChecked(True)

    def _load_niche_single_source(self, niche_name: str, source: str) -> list[str]:
        """Shared by _load_real_directory_links() / _load_yelp_directory_links()
        for the case a niche IS already selected in Quick Start above:
        generate that ONE niche's full top-100-city link list for just
        this one source ('yellowpages' or 'yelp'), using the "How many
        leads" control's value - the same generator Quick Start's "Use
        this niche" uses, just reachable directly from the Target section
        without an extra click. (el user kain 3ayez links aktar fe kol medina) -
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
            self._apply_max_protection_settings()

        # Same per-city budget as Quick Start's combined flow (see
        # _apply_all_sources_niche() above / generate_niche_urls_per_city()'s
        # docstring), just restricted to this ONE source so it gets the
        # WHOLE per-city budget instead of sharing it with the others.
        urls_per_city = self.target_results_spin.value()
        cities = self._collect_cities()
        start_urls = generate_niche_urls_per_city(
            niche_name, urls_per_city, cities=cities, sources=(source,),
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
        # docstring - da se7 el user kan 3ayez links aktar men el demo list).
        niche_name = self._current_niche_name_from_combo()
        if niche_name:
            urls = self._load_niche_single_source(niche_name, "yellowpages")
            QMessageBox.information(
                self, "Real Directory Search Links",
                f"'{niche_name}' is selected in Quick Start above — so {len(urls)} real yellowpages.com links "
                "were loaded covering the first page of all 100 cities (more if reach is raised), not the "
                "7 demo niches. Auto-quality is on.\n\nStart Campaign now.",
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
            "These are 7 verified working yellowpages.com links (checked in a real browser, not automated) "
            "returning real results for 7 of your niches — a quick one-city-per-niche test, not full "
            "100-city coverage.\n\n"
            "For full coverage of one niche, pick it in Quick Start above, then press this button again — "
            "it loads that niche's links for all 100 cities instead of these seven.\n\n"
            "The 'Repeat over' and field selectors (business_name / phone / website / address / city) were "
            "filled automatically on the Custom Selector tab — no Inspect Element needed. Auto-quality is on. "
            "Start Campaign now.",
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
                f"'{niche_name}' is selected in Quick Start above — so {len(urls)} real yelp.com links were "
                "loaded covering the first page of all 100 cities, not the 7 demo niches. The second "
                "phone fetch is on, and auto-quality.\n\n"
                "Note: Yelp blocks heavy traffic easily (a real run got HTTP 403 on nearly everything). "
                "Connection was switched to the protected browser and the pause between requests set to "
                "at least 2 seconds — slower run, much better odds. No 100% guarantee — Yelp actively "
                "resists scraping.\n\nStart Campaign now.",
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
        self._apply_max_protection_settings()
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        QMessageBox.information(
            self, "Yelp Search Links",
            "These are 7 real yelp.com links for 7 of your niches, same cities as the yellowpages button "
            "above — a quick one-city-per-niche test, not 100-city coverage.\n\n"
            "For full coverage of one niche, pick it in Quick Start above, then press this button again.\n\n"
            "'Repeat over' and the business-name selector were filled automatically on the Custom "
            "Selector tab. Phone numbers aren't on Yelp's search results page — LOGY will do a second "
            "fetch per business from its own Yelp page to get the phone (slightly slower). Auto-quality "
            "is on.\n\n"
            "Note: connection switched to the protected browser and the pause between requests set to at "
            "least 2 seconds — Yelp blocks FAST/HTTP easily; this lowers the odds of blocks but is no "
            "guarantee.\n\nStart Campaign now.",
        )

    def _load_all_sources_directory_links(self):
        """(load kol el links marratt wa7da) -
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
        (el user kan 3ayez links aktar men el 14 demo): the 14-link list below is a fixed 7-niche demo
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
        self._apply_max_protection_settings()  # this run includes yelp.com URLs too
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)
        QMessageBox.information(
            self, "All Sources (combined)",
            f"{len(combined)} links loaded in the same list — {len(self.REAL_DIRECTORY_SEARCH_LINKS)} from "
            f"yellowpages.com and {len(self.YELP_REAL_DIRECTORY_SEARCH_LINKS)} from yelp.com, for 7 of "
            "your niches (one city each — quick test, not 100-city coverage).\n\n"
            "For full coverage of one niche (all 100 cities, both sources), pick it in Quick Start above "
            "then press this button again — it loads that niche's links for all 100 cities automatically.\n\n"
            "Each link picks its own template automatically at fetch time — no need to run sources "
            "separately or merge results by hand. Auto-quality is on. Start Campaign now.",
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
            f"{len(self.HOUZZ_SEARCH_LINKS)} real houzz.com search links loaded — but only for 5 of your 15 "
            "niches (Houzz is a home-renovation directory — no solar, foundation repair, waterproofing "
            "or excavation categories, and none for professional niches like attorneys or dentists).\n\n"
            "Houzz regenerates its class names randomly on every deploy — no stable Custom Selector can "
            "survive. So this button switches the job to 'AI Auto-Extract' with 'Follow Links' on — "
            "LOGY pulls the results page, finds the company profile links, opens each profile and "
            "extracts its data with AI (phone and address appear directly in page text — verified on a "
            "real page).\n\n"
            "Note: requires an Anthropic or OpenAI API key saved on the AI Setup screen first, or LOGY "
            "refuses to start. Each profile page opened costs a real API call (not free like "
            "yellowpages or yelp).\n\n"
            "Straight limits: first results page only per category (~15 companies), and no real city "
            "filtering — Houzz returns the same nationwide list regardless of the city in the link "
            "(verified personally)."
        )

    # ------------------------------------------------------------------
    # STEP 2: DATA TO EXTRACT
    # ------------------------------------------------------------------
    # Tab order in self.extraction_tabs - named so jump-to-tab calls below
    # don't silently break if a tab gets added/reordered (this exact bug
    # happened once already when AI Auto-Extract was inserted at index 0).
    TAB_AI, TAB_SMART, TAB_CUSTOM, TAB_SCHEMA = 0, 1, 2, 3

    def _build_extraction_section(self) -> QWidget:
        w, layout = card("")

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
            QMessageBox.information(self, "Smart Extraction", "No fields recognized from the description. Try a clearer one.")
            return
        self.field_builder.load_fields(fields)
        self.extraction_tabs.setCurrentIndex(self.TAB_CUSTOM)  # jump to Custom Selector so the user fills in selectors

    def _validate_schema(self):
        ok, err, parsed = validate_json_schema(self.schema_input.toPlainText())
        if ok:
            self.schema_status_label.setText(f"Valid schema — {len(parsed)} fields")
            self.schema_status_label.setStyleSheet("color: #22C55E;")
            fields = [ExtractionField(name=k, selector="") for k in parsed.keys()]
            self.field_builder.load_fields(fields)
        else:
            self.schema_status_label.setText(f"Invalid: {err}")
            self.schema_status_label.setStyleSheet("color: #EF4444;")

    # ------------------------------------------------------------------
    # STEP 3: SCRAPING OPTIONS
    # ------------------------------------------------------------------
    def _build_options_section(self) -> QWidget:
        w, layout = card("")

        form = QFormLayout()
        self.fetcher_combo = QComboBox()
        self.fetcher_combo.addItem("Automatic (recommended)", FetcherMode.FAST_HTTP)
        self.fetcher_combo.addItem("Browser mode", FetcherMode.DYNAMIC_BROWSER)
        self.fetcher_combo.addItem("Protected browser (anti-block)", FetcherMode.STEALTH_BROWSER)
        form.addRow("Connection", self.fetcher_combo)
        layout.addLayout(form)

        # mat5alish lead tezhar marat keter - skip
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

        advanced = QWidget()
        adv_form = QFormLayout(advanced)

        self.headless_chk = QCheckBox("Run browser in background")
        self.headless_chk.setChecked(True)
        self.network_idle_chk = QCheckBox("Wait for pages to settle")
        self.disable_resources_chk = QCheckBox("Skip heavy assets (faster)")
        self.solve_cloudflare_chk = QCheckBox("Handle tough sites automatically")
        self.block_ads_chk = QCheckBox("Block ads & trackers (~3,500 domains)")
        self.dns_over_https_chk = QCheckBox("DNS-over-HTTPS (no DNS leaks on proxies)")
        self.real_chrome_chk = QCheckBox("Use real installed Chrome (stealth)")
        self.use_sessions_chk = QCheckBox("Persistent sessions (cookies + connection reuse)")
        self.use_sessions_chk.setChecked(True)

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
        adv_form.addRow(self.block_ads_chk)
        adv_form.addRow(self.dns_over_https_chk)
        adv_form.addRow(self.real_chrome_chk)
        adv_form.addRow(self.use_sessions_chk)
        adv_form.addRow("Parallel requests", self.concurrency_spin)
        adv_form.addRow("Pause between requests", self.delay_spin)
        adv_form.addRow("Timeout", self.timeout_spin)
        adv_form.addRow("Retries", self.retries_spin)
        adv_form.addRow("Request headers", self.headers_input)
        adv_form.addRow("Cookies", self.cookies_input)

        layout.addWidget(advanced)
        return w

    # ------------------------------------------------------------------
    # PROXY
    # ------------------------------------------------------------------
    def _build_proxy_section(self) -> QWidget:
        w, layout = card("")
        self.proxy_mode_combo = QComboBox()
        self.proxy_mode_combo.addItems([
            "Standard connection",
            "Fixed connection",
            "Rotating pool",
            "Rotating",
            "Maximum privacy",
            "Maximum (recommended)",
        ])
        self.proxy_list_input = QPlainTextEdit()
        self.proxy_list_input.setPlaceholderText("http://user:pass@host:port  (one per line)")
        self.proxy_list_input.setFixedHeight(70)
        layout.addWidget(self.proxy_mode_combo)
        layout.addWidget(self.proxy_list_input)

        # Tor settings (used only when the Tor mode is selected). Requests
        # tunnel through 127.0.0.1:<socks port>; every <rotate> requests the
        # control port is asked for a NEW circuit so the exit IP changes.
        tor_row = QHBoxLayout()
        tor_row.addWidget(QLabel("SOCKS port"))
        self.tor_socks_spin = QSpinBox()
        self.tor_socks_spin.setRange(1, 65535)
        self.tor_socks_spin.setValue(9050)
        tor_row.addWidget(self.tor_socks_spin)
        tor_row.addWidget(QLabel("Control port"))
        self.tor_control_spin = QSpinBox()
        self.tor_control_spin.setRange(1, 65535)
        self.tor_control_spin.setValue(9051)
        tor_row.addWidget(self.tor_control_spin)
        tor_row.addWidget(QLabel("Refresh connection every"))
        self.tor_rotate_spin = QSpinBox()
        self.tor_rotate_spin.setRange(0, 1000)
        self.tor_rotate_spin.setValue(10)
        self.tor_rotate_spin.setSuffix(" req")
        tor_row.addWidget(self.tor_rotate_spin)
        tor_row.addStretch(1)
        self.tor_row_widget = QWidget()
        self.tor_row_widget.setLayout(tor_row)
        self.tor_row_widget.setVisible(False)
        layout.addWidget(self.tor_row_widget)

        self.proxy_note = QLabel(
            "Your connection details are encrypted on your device only — never written to logs or exports.\n"
            "Maximum privacy mode: start Tor Browser (or tor.exe) before running — all traffic goes through the Tor network and the identity changes automatically.\n"
            "Maximum mode: your proxies + Tor in one rotation — when a site stops a request, the next one exits from a different, unblocked connection."
        )
        self.proxy_note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(self.proxy_note)
        self.proxy_mode_combo.currentIndexChanged.connect(self._on_proxy_mode_changed)
        return w

    def _on_proxy_mode_changed(self, index: int):
        is_tor = index in (4, 5)
        self.tor_row_widget.setVisible(is_tor)
        self.proxy_list_input.setVisible(index not in (0, 4))

    # ------------------------------------------------------------------
    # LIVE RUN PANEL
    # ------------------------------------------------------------------
    def _build_run_panel(self) -> QWidget:
        w, layout = card("")
        w.setObjectName("runPanel")
        layout.setContentsMargins(16, 14, 16, 14)

        # trading-desk status strip: pill + KPIs + controls
        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        self.status_label = QLabel("IDLE")
        self.status_label.setObjectName("statusRunning")
        status_row.addWidget(self.status_label)

        self.campaign_hint = QLabel("Campaign in progress")
        self.campaign_hint.setObjectName("pageSubtitle")
        status_row.addWidget(self.campaign_hint)
        status_row.addStretch(1)

        self.progress_bar = QProgressBar()

        _, self.pages_label = self._make_kpi(status_row, "SEARCHED", "0 / 0")
        _, self.records_label = self._make_kpi(status_row, "LEADS", "0")
        _, self.success_label = self._make_kpi(status_row, "SAVED", "0")
        _, self.failed_label = self._make_kpi(status_row, "BLOCKED & RETRIED", "0")
        _, self.elapsed_label = self._make_kpi(status_row, "TIME", "00:00:00")
        status_row.addStretch(1)

        self.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        status_row.addWidget(self.pause_btn)
        status_row.addWidget(self.stop_btn)
        self.export_btn = QPushButton("Get my leads")
        self.export_btn.clicked.connect(self._export_results)
        self.export_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        status_row.addWidget(self.export_btn)
        layout.addLayout(status_row)
        layout.addWidget(self.progress_bar)

        body_split = QSplitter(Qt.Orientation.Horizontal)

        feed_wrap = QWidget()
        feed_layout = QVBoxLayout(feed_wrap)
        feed_layout.setContentsMargins(0, 0, 0, 0)
        feed_layout.setSpacing(6)
        feed_header = QHBoxLayout()
        feed_title = QLabel("Activity")
        feed_title.setObjectName("feedTitle")
        feed_header.addWidget(feed_title)
        feed_header.addStretch(1)
        self.detailed_log_btn = QPushButton("Detailed log")
        self.detailed_log_btn.setObjectName("feedLink")
        self.detailed_log_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detailed_log_btn.clicked.connect(self._open_detailed_log)
        feed_header.addWidget(self.detailed_log_btn)
        feed_layout.addLayout(feed_header)

        feed_frame = QFrame()
        feed_frame.setObjectName("feedPanel")
        feed_frame_lay = QVBoxLayout(feed_frame)
        feed_frame_lay.setContentsMargins(10, 8, 10, 8)
        self.log_panel = ActivityFeed()
        feed_frame_lay.addWidget(self.log_panel)
        feed_layout.addWidget(feed_frame, 1)
        body_split.addWidget(feed_wrap)

        results_wrap = QWidget()
        results_layout = QVBoxLayout(results_wrap)
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(6)
        results_header = QHBoxLayout()
        self.results_count_label = QLabel("Latest leads — 0 collected")
        self.results_count_label.setObjectName("feedTitle")
        results_header.addWidget(self.results_count_label)
        results_header.addStretch(1)
        results_layout.addLayout(results_header)

        table_frame = QFrame()
        table_frame.setObjectName("tablePanel")
        table_frame_lay = QVBoxLayout(table_frame)
        table_frame_lay.setContentsMargins(10, 8, 10, 8)
        self.results_view = QTableView()
        self.results_view.setAlternatingRowColors(True)
        self.results_view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_view.setShowGrid(False)
        self.results_view.setWordWrap(False)  # one elided line per cell, no double-height rows
        vh = self.results_view.verticalHeader()
        vh.setVisible(False)          # concept table has no row-number gutter
        vh.setDefaultSectionSize(34)  # roomy rows instead of cramped 20px
        hh = self.results_view.horizontalHeader()
        hh.setFixedHeight(36)
        hh.setMinimumSectionSize(96)
        hh.setStretchLastSection(True)
        self._pill_delegate = QualityPillDelegate(self.results_view)
        table_frame_lay.addWidget(self.results_view)
        results_layout.addWidget(table_frame)
        body_split.addWidget(results_wrap)
        body_split.setStretchFactor(0, 1)
        body_split.setStretchFactor(1, 2)

        layout.addWidget(body_split, 1)
        return w

    def _open_detailed_log(self):
        self.open_logs.emit()

    def _make_kpi(self, row, micro: str, initial: str) -> tuple[QWidget, QLabel]:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        m = QLabel(micro)
        m.setObjectName("kpiMicro")
        val = QLabel(initial)
        val.setObjectName("kpiValue")
        v.addWidget(m)
        v.addWidget(val)
        row.addWidget(box)
        return box, val

    # ------------------------------------------------------------------
    # collecting config from the form
    # ------------------------------------------------------------------
    def _collect_target(self) -> TargetConfig | None:
        valid, invalid = parse_url_list(self.urls_input.toPlainText())
        if not valid:
            QMessageBox.warning(self, "Target", "Enter at least one valid URL.")
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
                "Need at least one field with a real selector on the Custom Selector tab.\n"
                "If you used Smart Extraction or JSON Schema, set the selectors there first.\n"
                "Or turn on AI Auto-Extract to work without selectors at all.",
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
            QMessageBox.warning(self, "AI Auto-Extract", "Pick at least one field under AI Auto-Extract.")
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

        proxy_mode_map = {0: "none", 1: "single", 2: "list", 3: "rotating", 4: "tor", 5: "hybrid"}
        proxy_mode = proxy_mode_map[self.proxy_mode_combo.currentIndex()]
        proxies = [] if proxy_mode in ("tor",) else [p.strip() for p in self.proxy_list_input.toPlainText().splitlines() if p.strip()]

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
            block_ads=self.block_ads_chk.isChecked(),
            dns_over_https=self.dns_over_https_chk.isChecked(),
            real_chrome=self.real_chrome_chk.isChecked(),
            use_sessions=self.use_sessions_chk.isChecked(),
            headers=headers,
            cookies=cookies,
            proxy=ProxyConfig(
                mode=proxy_mode,
                proxies=proxies,
                tor_socks_port=self.tor_socks_spin.value(),
                tor_control_port=self.tor_control_spin.value(),
                tor_rotate_every=self.tor_rotate_spin.value(),
            ),
            auto_qualify_leads=self.auto_qualify_chk.isChecked(),
            ai_extraction=self._collect_ai_extraction() or AIExtractionConfig(enabled=False),
            owner_lookup_enabled=self.owner_lookup_chk.isChecked(),
            skip_duplicate_leads=self.skip_duplicates_chk.isChecked(),
            pacing_mode=("fixed", "aimd", "burst")[self.pacing_combo.currentIndex()],
            identity_selection=("sticky", "weighted", "ucb1")[self.selection_combo.currentIndex()],
            use_identity_memory=self.identity_memory_chk.isChecked(),
            use_response_cache=self.response_cache_chk.isChecked(),
            discover_sitemap=self.sitemap_chk.isChecked(),
            solve_pow=self.pow_chk.isChecked(),
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
        QMessageBox.information(self, "Saved", f"Project saved as: {name}")

    def _options_to_dict(self, options: ScrapeOptions) -> dict:
        from app.core.storage import secrets as _secrets
        d = dict(options.__dict__)
        d.pop("proxy", None)  # rebuilt below - credentials never in plain JSON (audit C2)
        d["fetcher_mode"] = options.fetcher_mode.value
        d["proxy"] = {
            "mode": options.proxy.mode,
            # credentials live encrypted-at-rest; a redacted copy stays
            # readable for display/round-trip sanity checks
            "proxies_encrypted": _secrets.encrypt_proxy_list(options.proxy.proxies),
            "proxies_redacted": [_secrets.SecretStore.redact(p) for p in options.proxy.proxies],
            "tor_socks_port": options.proxy.tor_socks_port,
            "tor_control_port": options.proxy.tor_control_port,
            "tor_rotate_every": options.proxy.tor_rotate_every,
        }
        d["ai_extraction"] = {
            "enabled": options.ai_extraction.enabled,
            "field_names": options.ai_extraction.field_names,
            "provider": options.ai_extraction.provider,
            "ai_call_budget": options.ai_extraction.ai_call_budget,
        }
        return d

    def _resolve_ai_api_key_present(self, provider: str) -> bool:
        keys = self.db.get_setting("api_keys", {})
        return bool(keys.get(provider))

    def _start_scraping(self):
        if not engine.ENGINE_AVAILABLE:
            QMessageBox.critical(
                self, "Engine not ready",
                f"the fetch engine isn't installed in this environment.\n\n{engine.ENGINE_IMPORT_ERROR}\n\n"
                "Run: pip install scrapling && scrapling install",
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
                f"No API key saved as '{options.ai_extraction.provider}' on AI Setup.\n"
                "Add it first, or switch to Custom Selector.",
            )
            return
        if self.owner_lookup_chk.isChecked() and not self._resolve_ai_api_key_present(options.ai_extraction.provider):
            QMessageBox.warning(
                self, "Owner Lookup",
                f"'Find owner contacts' needs an API key saved as '{options.ai_extraction.provider}' "
                "on AI Setup (pick the provider on the AI Auto-Extract tab to change it).\n"
                "Add it first, or turn this option off.",
            )
            return
        container = self._collect_container()

        self.run_panel.setVisible(True)
        self.log_panel.clear()
        self.status_label.setText("RUNNING")
        self.campaign_hint.setText("Collecting leads · auto-protected")
        self.start_btn.setVisible(False)
        self.pause_btn.setVisible(True)
        self.stop_btn.setVisible(True)
        self.pause_btn.setText("Pause")

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
        self._bind_quality_pills()

        # start from a clean, moving bar: 0% now, real percentage as the
        # progress signal reports done/known-work, 100% on finish
        self.progress_bar.setValue(0)
        self.pages_label.setText("0 / 0")
        self.records_label.setText("0")
        self.success_label.setText("0")
        self.failed_label.setText("0")

        self._elapsed_timer = self.startTimer(1000)

        self.job_manager.start_prepared_job()

    def _resume_job(self, job_id: int):
        """Resume an INTERRUPTED job from its checkpoint (audit C3/H1):
        same signal wiring as a fresh start, but JobManager builds the
        worker from the persisted spec + remaining pending queue."""
        if self.job_manager.is_running:
            QMessageBox.information(self, "Resume", "مهمة تانية شغالة دلوقتي - أوقفها الأول.")
            return
        try:
            job_id, worker = self.job_manager.prepare_resume_job(job_id)
        except RuntimeError as e:
            QMessageBox.warning(self, "Resume", str(e))
            return

        self.run_panel.setVisible(True)
        self.log_panel.clear()
        self.status_label.setText("RUNNING")
        self.campaign_hint.setText("Resuming interrupted campaign · auto-protected")
        self.start_btn.setVisible(False)
        self.pause_btn.setVisible(True)
        self.stop_btn.setVisible(True)
        self.pause_btn.setText("Pause")
        self.current_job_id = job_id
        self._job_start_ts = __import__("time").time()

        worker.log.connect(self._on_log)
        worker.progress.connect(self._on_progress)
        worker.result_ready.connect(self._on_result)
        worker.status_changed.connect(self._on_status_changed)
        worker.finished.connect(self._on_finished)

        self.results_model = ResultsTableModel(self.db, job_id)
        self.results_view.setModel(self.results_model)
        self._bind_quality_pills()
        self.progress_bar.setValue(0)

        self._elapsed_timer = self.startTimer(1000)
        self.job_manager.start_prepared_job()

    def timerEvent(self, event):
        if hasattr(self, "_job_start_ts") and self.job_manager.is_running:
            import time
            elapsed = int(time.time() - self._job_start_ts)
            h, rem = divmod(elapsed, 3600)
            m, s = divmod(rem, 60)
            self.elapsed_label.setText(f"{h:02}:{m:02}:{s:02}")

    def _toggle_pause(self):
        currently_paused = self.pause_btn.text() == "Resume"
        self.job_manager.pause(not currently_paused)
        from app.ui.widgets import icons as _ic
        self.pause_btn.setText("Resume" if not currently_paused else "Pause")
        self.pause_btn.setIcon(_ic.icon("play" if not currently_paused else "pause", "#8FA3C0", 17))

    def _stop_scraping(self):
        self.job_manager.stop()

    def _on_log(self, level: str, message: str):
        self.log_panel.append_entry(level, message)

    def _on_progress(self, pages_done, pages_total, records_ok, records_failed):
        self.pages_label.setText(f"{pages_done} / {pages_total}")
        self.records_label.setText(f"{records_ok + records_failed}")
        self.success_label.setText(f"{records_ok}")
        self.failed_label.setText(f"{records_failed}")
        if pages_total:
            self.progress_bar.setValue(int(pages_done / pages_total * 100))

    def _on_result(self, record: dict):
        if self.results_model:
            self.results_model.append_live_result()
            self._bind_quality_pills()
            self.results_count_label.setText(f"Latest leads — {self.results_model.total_count} collected")

    def _bind_quality_pills(self):
        # auto-qualify saves digital_label; that column only appears after the
        # first qualified record, so re-bind after every model reset
        if not self.results_model:
            return
        for col, name in enumerate(self.results_model._columns):
            if "digital_label" in name or "quality" in name:
                self.results_view.setItemDelegateForColumn(col, self._pill_delegate)
                self.results_view.horizontalHeader().resizeSection(col, 130)
        self._fit_result_columns()

    def _fit_result_columns(self):
        """Give the early columns usable widths (business name / phone / site
        get squeezed to ~100px otherwise) while throttling the pass so a
        1,000-row run doesn't re-measure on every single result."""
        model = self.results_model
        if not model:
            return
        total = model.total_count
        if total not in (0, 1) and total % 10 != 0:
            return
        hh = self.results_view.horizontalHeader()
        for col, name in enumerate(model._columns):
            hint = self.results_view.sizeHintForColumn(col) + 14
            cap = 240 if "signals" in name else 150
            hh.resizeSection(col, min(max(hint, 96), cap))
        hh.setStretchLastSection(True)

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
        # close the bar out: whatever the last emitted percentage was,
        # the run is over - show it complete rather than frozen mid-way
        # (early-end/stop paths never emit a final 100% themselves).
        self.progress_bar.setValue(100)
        if hasattr(self, "_elapsed_timer"):
            self.killTimer(self._elapsed_timer)

    def _export_results(self):
        if not self.current_job_id:
            QMessageBox.information(self, "Export", "No results yet.")
            return
        fmt, ok = self._ask_export_format()
        if not ok:
            return
        # "odoo_xlsx"/"odoo_xls" map to REAL .xlsx/.xls files respectively
        # (see exporter.export_odoo_xlsx / export_odoo_xls) - only the
        # internal EXPORTERS key has the odoo_ prefix, the file extension
        # on disk must match the actual bytes written or Excel/Odoo may
        # reject or mis-parse it. (el user 3ayez xls aslan) - Odoo's own
        # downloadable CRM Lead template is itself a "crm_lead 1.xls"
        # file, so that's the default Odoo option now instead of .xlsx.
        ODOO_EXTENSIONS = {"odoo_xlsx": "xlsx", "odoo_xls": "xls"}
        extension = ODOO_EXTENSIONS.get(fmt, fmt)
        default_name = f"crm_leads.{extension}" if fmt in ODOO_EXTENSIONS else f"results.{extension}"
        path, _ = QFileDialog.getSaveFileName(self, "Export results", default_name, f"*.{extension}")
        if not path:
            return
        # Both odoo_* formats can carry extra required columns (Channel,
        # Source, Sales Team, ...) that are required only on THIS user's
        # own Odoo instance, not part of Odoo's stock crm.lead import
        # template - see exporter.py's extra_fields_from_settings()
        # docstring. Configured from Settings -> "Odoo Export"'s table (db
        # setting "odoo_extra_fields"); never guessed here. Reads through
        # the same helper Settings uses so a value set there before this
        # export runs (or a still-unmigrated legacy "odoo_channel_value")
        # is picked up identically in both places.
        extra_kwargs = {}
        if fmt in ODOO_EXTENSIONS:
            extra_kwargs["extra_fields"] = exporter.extra_fields_from_settings(self.db.get_setting)
        try:
            count = exporter.export(fmt, self.db.iter_all_results(self.current_job_id), path, **extra_kwargs)
            QMessageBox.information(self, "Export", f"Exported {count} records to:\n{path}")
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
