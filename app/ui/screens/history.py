"""
History screen.

Redesigned into two tabs:
  - "Job Runs"      the original per-job history (unchanged behavior).
  - "Leads History" NEW - every lead ever generated, across every job and
                    project, de-duplicated by app/core/engine/dedupe.py's
                    fingerprint. This is the actual answer to "عايز اعمل
                    هيستوري لليدز اللي طلعت مسبقا متتكررش كل ما نجينيريت
                    ليدز": app/core/job_manager.py already skips re-saving a
                    lead whose fingerprint is in here (see its main loop),
                    this tab is where that memory becomes visible/manageable
                    - searchable, and clearable via "Clear Leads History"
                    for when the user actually wants old leads eligible for
                    re-generation again (e.g. starting a fresh campaign).
"""
from __future__ import annotations

import datetime
import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget, QMessageBox,
)
from app.core.storage.db import Database

JOB_COLUMNS = ["Job", "Project", "Status", "Started", "Finished", "Pages", "Records", "Errors"]
LEAD_COLUMNS = ["Name", "Company", "Email", "Phone", "Website", "First Seen", "Last Seen", "Times Seen"]


def _fmt_ts(ts) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def _lead_field(data: dict, *keys: str) -> str:
    lower = {str(k).lower(): v for k, v in data.items()}
    for k in keys:
        v = lower.get(k)
        if v not in (None, ""):
            return str(v)
    return "-"


class HistoryScreen(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        header_row = QHBoxLayout()
        title = QLabel("History")
        title.setObjectName("pageTitle")
        header_row.addWidget(title)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_jobs_tab(), "Job Runs")
        self.tabs.addTab(self._build_leads_tab(), "Leads History")

        self.refresh()

    # ------------------------------------------------------------------
    # Job Runs tab (unchanged behavior, just moved into its own tab)
    # ------------------------------------------------------------------
    def _build_jobs_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        self.jobs_table = QTableWidget(0, len(JOB_COLUMNS))
        self.jobs_table.setHorizontalHeaderLabels(JOB_COLUMNS)
        self.jobs_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.jobs_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.jobs_table.setAlternatingRowColors(True)
        layout.addWidget(self.jobs_table, 1)
        return w

    # ------------------------------------------------------------------
    # Leads History tab (new)
    # ------------------------------------------------------------------
    def _build_leads_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.leads_count_label = QLabel("")
        self.leads_count_label.setObjectName("pageSubtitle")
        toolbar.addWidget(self.leads_count_label)
        toolbar.addStretch(1)

        self.leads_search = QLineEdit()
        self.leads_search.setPlaceholderText("Search leads history (name, company, email, phone...)")
        self.leads_search.setFixedWidth(320)
        self.leads_search.textChanged.connect(self._refresh_leads)
        toolbar.addWidget(self.leads_search)

        clear_btn = QPushButton("Clear Leads History")
        clear_btn.setObjectName("dangerButton")
        clear_btn.setToolTip(
            "Forgets every lead LOGY has ever remembered generating. After clearing, previously "
            "generated leads become eligible to show up again in a new scrape (skip_duplicate_leads "
            "checks against this history)."
        )
        clear_btn.clicked.connect(self._clear_leads_history)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        note = QLabel(
            "Every lead LOGY has ever extracted, across every job and project. A new scrape skips "
            "re-saving any lead already listed here (unless 'Skip leads already generated before' "
            "is unchecked in Scraping Options)."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        self.leads_table = QTableWidget(0, len(LEAD_COLUMNS))
        self.leads_table.setHorizontalHeaderLabels(LEAD_COLUMNS)
        self.leads_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.leads_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.leads_table.setAlternatingRowColors(True)
        layout.addWidget(self.leads_table, 1)
        return w

    def _clear_leads_history(self):
        reply = QMessageBox.question(
            self, "Clear Leads History",
            "متأكد إنك عايز تمسح هيستوري الليدز كله؟\n"
            "بعد المسح، أي ليد طلع قبل كده ممكن يظهر تاني في سكرابنج جديد.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.clear_lead_history()
            self._refresh_leads()

    def _refresh_leads(self):
        search = self.leads_search.text().strip() or None
        leads = self.db.list_lead_history(search=search, limit=5000)
        total = self.db.count_lead_history()
        shown = len(leads)
        self.leads_count_label.setText(
            f"{total} lead(s) remembered" if not search else f"{shown} of {total} lead(s) match"
        )

        self.leads_table.setRowCount(len(leads))
        for row, lead in enumerate(leads):
            try:
                data = json.loads(lead["data_json"])
            except (TypeError, ValueError):
                data = {}
            values = [
                _lead_field(data, "name", "contact_name", "full_name"),
                _lead_field(data, "company_name", "company", "business_name"),
                _lead_field(data, "email", "e-mail", "mail"),
                _lead_field(data, "phone", "mobile", "phone_number"),
                _lead_field(data, "website", "site", "url"),
                _fmt_ts(lead["first_seen_at"]),
                _fmt_ts(lead["last_seen_at"]),
                str(lead["times_seen"]),
            ]
            for col, value in enumerate(values):
                self.leads_table.setItem(row, col, QTableWidgetItem(value))

    def refresh(self):
        jobs = self.db.list_jobs(limit=500)
        self.jobs_table.setRowCount(len(jobs))
        for row, j in enumerate(jobs):
            values = [
                f"#{j['id']}",
                str(j["project_id"] or "-"),
                j["status"].upper(),
                _fmt_ts(j["started_at"]),
                _fmt_ts(j["finished_at"]),
                f"{j['pages_done']}/{j['pages_total']}",
                str(j["records_ok"]),
                str(j["records_failed"]),
            ]
            for col, value in enumerate(values):
                self.jobs_table.setItem(row, col, QTableWidgetItem(value))

        self._refresh_leads()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
