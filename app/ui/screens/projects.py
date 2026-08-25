from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QMessageBox, QInputDialog,
)
from PySide6.QtCore import Signal

from app.core.storage.db import Database


class ProjectsScreen(QWidget):
    open_project = Signal(int)   # project_id -> New Scrape screen should load it

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Projects")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.run_btn = QPushButton("Run")
        self.duplicate_btn = QPushButton("Duplicate")
        self.rename_btn = QPushButton("Rename")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("dangerButton")
        for b in (self.open_btn, self.run_btn, self.duplicate_btn, self.rename_btn, self.delete_btn):
            actions.addWidget(b)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.open_btn.clicked.connect(self._open_selected)
        self.duplicate_btn.clicked.connect(self._duplicate_selected)
        self.rename_btn.clicked.connect(self._rename_selected)
        self.delete_btn.clicked.connect(self._delete_selected)

        self.refresh()

    def _selected_id(self) -> int | None:
        item = self.list_widget.currentItem()
        return item.data(1000) if item else None

    def refresh(self):
        self.list_widget.clear()
        projects = self.db.list_projects()
        if not projects:
            self.list_widget.addItem("No scraping projects yet. Create your first one from New Scrape.")
            return
        for p in projects:
            last_run = "never" if not p["last_run_at"] else "ran"
            item = QListWidgetItem(f"{p['name']}  ·  last result count: {p['last_result_count']}  ·  {last_run}")
            item.setData(1000, p["id"])
            self.list_widget.addItem(item)

    def _open_selected(self):
        pid = self._selected_id()
        if pid:
            self.open_project.emit(pid)

    def _duplicate_selected(self):
        pid = self._selected_id()
        if not pid:
            return
        project = self.db.get_project(pid)
        config = json.loads(project["config_json"])
        self.db.create_project(f"{project['name']} (copy)", config)
        self.refresh()

    def _rename_selected(self):
        pid = self._selected_id()
        if not pid:
            return
        project = self.db.get_project(pid)
        new_name, ok = QInputDialog.getText(self, "Rename project", "New name:", text=project["name"])
        if ok and new_name.strip():
            self.db.update_project(pid, new_name.strip(), json.loads(project["config_json"]))
            self.refresh()

    def _delete_selected(self):
        pid = self._selected_id()
        if not pid:
            return
        if QMessageBox.question(self, "Delete project", "متأكد إنك عايز تحذف المشروع ده؟") == QMessageBox.StandardButton.Yes:
            self.db.delete_project(pid)
            self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
