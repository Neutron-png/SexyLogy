"""
ICP Campaigns screen - the ICP-driven discovery workflow:

  upload ICP (PDF/DOCX/TXT) -> structured draft (deterministic, optional
  one AI call via the existing AI Setup keys) -> USER REVIEW (editable
  fields) -> discovery queries generated -> LOGY Search (zero-cost
  internal service) -> qualified source URLs -> handoff to the EXISTING
  crawler (New Campaign) for lead extraction.

The crawler stays the only page fetcher; this screen only discovers
sources and qualifies them. Discovery runs in a background QThread.
"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox,
    QGroupBox, QFormLayout, QCheckBox, QSpinBox, QComboBox, QApplication,
)

from app.core.search import icp
from app.core.search.service import SearchService
from app.core.search.schema import SearchRequest
from app.core.storage.db import Database

ICP_FIELDS = icp.ICP_FIELDS
FIELD_LABELS = [
    ("industries", "Target industries (comma-separated)"),
    ("keywords", "Keywords"),
    ("locations", "Locations (cities/regions)"),
    ("exclusions", "Exclusions (what NOT to include)"),
    ("company_size", "Company size"),
    ("roles", "Decision-maker roles (from published pages only)"),
    ("qualifying_signals", "Qualifying signals"),
    ("disqualifying_signals", "Disqualifying signals"),
]

MAX_DOC_SIZE = 10 * 1024 * 1024  # 10 MB upload cap - DoS guard


class _DiscoveryWorker(QObject):
    """SearchService call off the GUI thread (search -> normalize ->
    dedupe -> qualify). Emits a structured summary; the thread pattern
    mirrors JobManager's prepare/finished wiring."""
    done = Signal(list)       # qualified rows
    failed = Signal(str)

    def __init__(self, service: SearchService, queries: list[str], profile: dict,
                 max_sources: int):
        super().__init__()
        self.service = service
        self.queries = queries
        self.profile = profile
        self.max_sources = max_sources

    def run(self):
        try:
            requests = [SearchRequest(query=q, num_results=10) for q in self.queries]
            responses = self.service.search_many(requests)
            rows = []
            seen_domains: set[str] = set()
            for resp in responses:
                if isinstance(resp, Exception):
                    continue
                for r in resp.results.organic:
                    q = icp.qualify_source(r.url, r.title, r.snippet or "", self.profile)
                    from app.core.search.urls import domain_of
                    d = domain_of(r.url)
                    if q["score"] <= 0 or d in seen_domains:
                        continue
                    seen_domains.add(d)
                    rows.append({
                        "url": r.url, "title": r.title, "snippet": r.snippet or "",
                        "score": q["score"], "reasons": "; ".join(q["reasons"]),
                        "provider": resp.provider, "query": resp.query,
                    })
            rows.sort(key=lambda x: -x["score"])
            self.done.emit(rows[:self.max_sources])
        except Exception as e:
            self.failed.emit(str(e))


class IcpScreen(QWidget):
    send_to_crawler = Signal(list)   # selected source URLs -> main_window

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self._icp_profile: dict = icp.empty_profile()
        self._source_file = ""
        self._source_hash = ""
        self._thread: QThread | None = None
        self._worker: _DiscoveryWorker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("ICP Campaigns")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # ---- upload / saved ICPs ----
        top_row = QHBoxLayout()
        self.upload_btn = QPushButton("Upload ICP (PDF / DOCX / TXT)")
        self.upload_btn.setObjectName("primaryButton")
        self.upload_btn.clicked.connect(self._upload)
        top_row.addWidget(self.upload_btn)
        self.saved_combo = QComboBox()
        self._reload_saved()
        self.saved_combo.currentIndexChanged.connect(self._open_saved)
        top_row.addWidget(QLabel("Saved ICPs:"))
        top_row.addWidget(self.saved_combo, 1)
        layout.addLayout(top_row)

        self.source_label = QLabel("No ICP loaded - upload a document or pick a saved one.")
        self.source_label.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(self.source_label)

        # ---- editable structured profile ----
        group = QGroupBox("What I understood - review and edit (this is what drives discovery)")
        form = QFormLayout(group)
        self.field_inputs: dict[str, QLineEdit] = {}
        for key, label in FIELD_LABELS:
            edit = QLineEdit()
            form.addRow(label, edit)
            self.field_inputs[key] = edit
        self.ai_chk = QCheckBox("Use AI (one call, your own saved key) to understand the document")
        form.addRow(self.ai_chk)
        layout.addWidget(group)

        # ---- discovery controls ----
        controls = QHBoxLayout()
        self.max_queries = QSpinBox()
        self.max_queries.setRange(1, 50)
        self.max_queries.setValue(20)
        self.max_sources = QSpinBox()
        self.max_sources.setRange(1, 200)
        self.max_sources.setValue(20)
        discover_btn = QPushButton("Discover Sources")
        discover_btn.setObjectName("primaryButton")
        discover_btn.clicked.connect(self._discover)
        controls.addWidget(QLabel("Max queries"))
        controls.addWidget(self.max_queries)
        controls.addWidget(QLabel("Max sources"))
        controls.addWidget(self.max_sources)
        controls.addStretch(1)
        controls.addWidget(discover_btn)
        layout.addLayout(controls)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(self.status_label)

        # ---- discovered sources table ----
        cols = ["Score", "Title", "URL", "Why", "Provider"]
        self.sources_table = QTableWidget(0, len(cols))
        self.sources_table.setHorizontalHeaderLabels(cols)
        self.sources_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.sources_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.sources_table.setAlternatingRowColors(True)
        layout.addWidget(self.sources_table, 1)

        bottom = QHBoxLayout()
        self.save_btn = QPushButton("Save ICP")
        self.save_btn.clicked.connect(self._save_icp)
        bottom.addWidget(self.save_btn)
        bottom.addStretch(1)
        self.crawl_btn = QPushButton("Crawl Selected (send to New Campaign)")
        self.crawl_btn.setObjectName("primaryButton")
        self.crawl_btn.setEnabled(False)
        self.crawl_btn.clicked.connect(self._send_selected)
        bottom.addWidget(self.crawl_btn)
        layout.addLayout(bottom)

        self.refresh()

    # ---------------- profile input ----------------
    def refresh(self):
        self._reload_saved()

    def _reload_saved(self):
        self.saved_combo.blockSignals(True)
        self.saved_combo.clear()
        self.saved_combo.addItem("— pick a saved ICP —", None)
        for p in icp.list_profiles(self.db):
            self.saved_combo.addItem(f"{p['name']} ({p['source_file']})", p["id"])
        self.saved_combo.blockSignals(False)

    def _open_saved(self, _idx):
        pid = self.saved_combo.currentData()
        if not pid:
            return
        for p in icp.list_profiles(self.db):
            if p["id"] == pid:
                self._source_hash = p["source_hash"]
                self._set_profile(p["profile"], f"محفوظ: {p['name']} (from {p['source_file']})")
                return

    def _set_profile(self, profile: dict, source_text: str):
        self._icp_profile = profile
        self._source_file = source_text
        for key, _label in FIELD_LABELS:
            self.field_inputs[key].setText(", ".join(profile.get(key, [])))
        self.source_label.setText(source_text)

    def _upload(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Upload ICP", "", "Documents (*.pdf *.docx *.txt)")
        if not path:
            return
        if Path(path).stat().st_size > MAX_DOC_SIZE:
            QMessageBox.warning(self, "ICP", "الملف أكبر من الحد المسموح (10 MB).")
            return
        try:
            text = icp.extract_text(path)
        except ValueError as e:
            QMessageBox.critical(self, "ICP", str(e))
            return
        if not text.strip():
            QMessageBox.warning(self, "ICP", "المتند مش مقروء أو فاضي.")
            return
        if self.ai_chk.isChecked():
            profile, notes = icp.ai_profile_from_text(text, self.db)
            note = "; ".join(notes)
        else:
            profile = icp.draft_from_text(text)
            note = "deterministic draft - عدّل الحقول بنفسك"
        self._source_hash = icp.file_hash(path)
        self._set_profile(profile, f"{Path(path).name}" + (f"  ({note})" if note else ""))

    def _profile_from_ui(self) -> dict:
        return icp.profile_from_ui({k: w.text() for k, w in self.field_inputs.items()})

    def _save_icp(self):
        profile = self._profile_from_ui()
        if not any(profile.values()):
            QMessageBox.warning(self, "Save ICP", "الـ ICP فاضي - عبّي حقل واحد على الأقل.")
            return
        name = Path(self._source_file).stem or f"ICP {len(icp.list_profiles(self.db)) + 1}"
        pid = icp.save_profile(self.db, name, self._source_file or "manual",
                               self._source_hash, profile)
        self._reload_saved()
        QMessageBox.information(self, "Save ICP", f"ICP saved as: {name}")

    # ---------------- discovery ----------------
    def _discover(self):
        if self._thread is not None and self._thread.isRunning():
            QMessageBox.information(self, "Discovery", "اكتشاف شغال بالفعل.")
            return
        profile = self._profile_from_ui()
        queries = icp.generate_queries(profile, max_queries=self.max_queries.value())
        if not queries:
            QMessageBox.warning(
                self, "Discovery",
                "مفيش industries أو keywords في الـ ICP - عبّي حقل واحد منهم الأول.")
            return
        self.status_label.setText(f"جاري الاكتشاف ({len(queries)} query)...")
        self.sources_table.setRowCount(0)
        self.crawl_btn.setEnabled(False)

        service = SearchService(self.db)
        self._thread = QThread()
        self._worker = _DiscoveryWorker(service, queries, profile, self.max_sources.value())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_discovered)
        self._worker.failed.connect(self._on_discovery_failed)
        self._worker.done.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.start()

    def _on_discovered(self, rows: list):
        self.sources_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [str(r["score"]), r["title"], r["url"], r["reasons"], r["provider"]]
            for col, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, r["url"])
                self.sources_table.setItem(i, col, item)
        self.crawl_btn.setEnabled(bool(rows))
        queries_count = len({r["query"] for r in rows})
        self.status_label.setText(
            f"{len(rows)} مصدر مؤهل (من {queries_count}+ استعلام)"
            if rows else "مفيش مصادر مؤهلة - جرب keywords أوسع أو شيل exclusions")

    def _on_discovery_failed(self, error: str):
        self.status_label.setText(f"فشل الاكتشاف: {error[:200]}")
        QMessageBox.critical(self, "Discovery", f"البحث فشل:\n{error[:400]}")

    # ---------------- handoff ----------------
    def _send_selected(self):
        urls = []
        for row in range(self.sources_table.rowCount()):
            item = self.sources_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole):
                urls.append(item.data(Qt.ItemDataRole.UserRole))
        if not urls:
            QMessageBox.information(self, "Crawl", "اختار مصدر واحد على الأقل من الجدول.")
            return
        self.send_to_crawler.emit(urls)
