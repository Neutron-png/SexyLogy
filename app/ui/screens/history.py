from __future__ import annotations

import datetime

from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QHeaderView
from app.core.storage.db import Database

COLUMNS = ["Job", "Project", "Status", "Started", "Finished", "Pages", "Records", "Errors"]


class HistoryScreen(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("History")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        self.refresh()

    def refresh(self):
        jobs = self.db.list_jobs(limit=500)
        self.table.setRowCount(len(jobs))
        for row, j in enumerate(jobs):
            started = datetime.datetime.fromtimestamp(j["started_at"]).strftime("%Y-%m-%d %H:%M")
            finished = (
                datetime.datetime.fromtimestamp(j["finished_at"]).strftime("%Y-%m-%d %H:%M")
                if j["finished_at"] else "-"
            )
            values = [
                f"#{j['id']}",
                str(j["project_id"] or "-"),
                j["status"].upper(),
                started,
                finished,
                f"{j['pages_done']}/{j['pages_total']}",
                str(j["records_ok"]),
                str(j["records_failed"]),
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
