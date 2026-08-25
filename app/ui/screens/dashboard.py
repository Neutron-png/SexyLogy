from __future__ import annotations

from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem
from app.core.storage.db import Database


def stat_card(title: str, value: str) -> QWidget:
    w = QWidget()
    w.setObjectName("card")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(16, 14, 16, 14)
    t = QLabel(title)
    t.setStyleSheet("color: #8B95A7; font-size: 12px;")
    v = QLabel(value)
    v.setStyleSheet("color: #E5E9F0; font-size: 26px; font-weight: 700;")
    layout.addWidget(t)
    layout.addWidget(v)
    return w


class DashboardScreen(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(24, 20, 24, 20)
        self.layout_.setSpacing(14)

        title = QLabel("Dashboard")
        title.setObjectName("pageTitle")
        self.layout_.addWidget(title)

        self.cards_row = QHBoxLayout()
        self.layout_.addLayout(self.cards_row)

        recent_label = QLabel("Recent Jobs")
        recent_label.setObjectName("sectionTitle")
        self.layout_.addWidget(recent_label)
        self.recent_list = QListWidget()
        self.layout_.addWidget(self.recent_list, 1)

        self.refresh()

    def refresh(self):
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        jobs = self.db.list_jobs(limit=1000)
        total = len(jobs)
        total_records = sum(j["records_ok"] for j in jobs)
        successful = sum(1 for j in jobs if j["status"] == "completed")
        failed = sum(1 for j in jobs if j["status"] in ("failed", "stopped"))

        for card in (
            stat_card("Total Scrapes", str(total)),
            stat_card("Total Records", str(total_records)),
            stat_card("Successful Runs", str(successful)),
            stat_card("Failed Runs", str(failed)),
        ):
            self.cards_row.addWidget(card)

        self.recent_list.clear()
        if not jobs:
            self.recent_list.addItem("No scraping jobs yet. Start one from New Scrape.")
            return
        for j in jobs[:20]:
            item = QListWidgetItem(
                f"Job #{j['id']}  ·  {j['status'].upper()}  ·  {j['records_ok']} records  ·  {j['pages_done']} pages"
            )
            self.recent_list.addItem(item)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
