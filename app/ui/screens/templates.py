from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton

from app.core.storage.db import Database


class TemplatesScreen(QWidget):
    use_template = Signal(dict)  # emits the template's field config to New Scrape

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Templates")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel("Templates preconfigure extraction fields - not every field will exist on every site.")
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        row = QHBoxLayout()
        self.use_btn = QPushButton("Use in New Scrape")
        self.use_btn.setObjectName("primaryButton")
        self.use_btn.clicked.connect(self._use_selected)
        row.addWidget(self.use_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.refresh()

    def refresh(self):
        self.list_widget.clear()
        for t in self.db.list_templates():
            config = json.loads(t["config_json"])
            n_fields = len(config.get("fields", []))
            label = f"{t['name']}  ({'built-in' if t['builtin'] else 'custom'})  ·  {n_fields} fields"
            item = QListWidgetItem(label)
            item.setData(1000, t["id"])
            self.list_widget.addItem(item)

    def _use_selected(self):
        item = self.list_widget.currentItem()
        if not item:
            return
        tpl_id = item.data(1000)
        for t in self.db.list_templates():
            if t["id"] == tpl_id:
                self.use_template.emit(json.loads(t["config_json"]))
                return

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
