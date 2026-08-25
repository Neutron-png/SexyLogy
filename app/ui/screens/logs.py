from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QPushButton, QFileDialog, QMessageBox,
)
from app.core.storage.db import Database
from app.ui.widgets.log_panel import LogPanel


class LogsScreen(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Logs")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        toolbar = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search logs...")
        self.level_combo = QComboBox()
        self.level_combo.addItems(["All levels", "INFO", "SUCCESS", "WARNING", "ERROR", "DEBUG"])
        refresh_btn = QPushButton("Refresh")
        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("dangerButton")
        export_btn = QPushButton("Export")
        toolbar.addWidget(self.search_input, 1)
        toolbar.addWidget(self.level_combo)
        toolbar.addWidget(refresh_btn)
        toolbar.addWidget(export_btn)
        toolbar.addWidget(clear_btn)
        layout.addLayout(toolbar)

        self.log_panel = LogPanel()
        layout.addWidget(self.log_panel, 1)

        refresh_btn.clicked.connect(self.refresh)
        clear_btn.clicked.connect(self._clear)
        export_btn.clicked.connect(self._export)
        self.search_input.textChanged.connect(self.refresh)
        self.level_combo.currentTextChanged.connect(self.refresh)

        self.refresh()

    def refresh(self):
        level = self.level_combo.currentText()
        level = None if level == "All levels" else level
        rows = self.db.list_logs(level=level, limit=2000)
        query = self.search_input.text().strip().lower()
        if query:
            rows = [r for r in rows if query in r["message"].lower()]
        self.log_panel.clear()
        for r in reversed(rows):  # chronological
            self.log_panel.append_entry(r["level"], r["message"])

    def _clear(self):
        if QMessageBox.question(self, "Clear logs", "امسح كل السجلات؟") == QMessageBox.StandardButton.Yes:
            self.db.clear_logs()
            self.refresh()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export logs", "logy_logs.txt", "*.txt")
        if not path:
            return
        rows = self.db.list_logs(limit=100000)
        with open(path, "w", encoding="utf-8") as f:
            for r in reversed(rows):
                f.write(f"[{r['level']}] {r['message']}\n")
        QMessageBox.information(self, "Export", f"تم التصدير إلى:\n{path}")

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
