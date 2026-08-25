from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QFormLayout, QFileDialog, QMessageBox, QTabWidget,
)

from app.core.storage.db import Database
from app.core.engine import scrapling_adapter as engine


class SettingsScreen(QWidget):
    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("Settings")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        tabs = QTabWidget()
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._scraping_tab(), "Scraping")
        tabs.addTab(self._browser_tab(), "Browser")
        tabs.addTab(self._storage_tab(), "Storage")
        tabs.addTab(self._advanced_tab(), "Advanced")
        layout.addWidget(tabs, 1)

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        theme = QComboBox()
        theme.addItems(["Dark"])
        theme.setEnabled(False)
        language = QComboBox()
        language.addItems(["English", "العربية"])
        export_folder = QLineEdit(self.db.get_setting("default_export_folder", str(Path.home())))
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(lambda: self._pick_folder(export_folder))
        row = QHBoxLayout()
        row.addWidget(export_folder)
        row.addWidget(browse_btn)
        auto_save = QCheckBox("Auto-save projects")
        auto_save.setChecked(self.db.get_setting("auto_save_projects", True))
        auto_save.toggled.connect(lambda v: self.db.set_setting("auto_save_projects", v))
        export_folder.textChanged.connect(lambda v: self.db.set_setting("default_export_folder", v))

        form.addRow("Theme", theme)
        form.addRow("Language", language)
        form.addRow("Default export folder", row)
        form.addRow(auto_save)
        return w

    def _scraping_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        fetcher = QComboBox()
        fetcher.addItems(["fast_http", "dynamic", "stealth"])
        fetcher.setCurrentText(self.db.get_setting("default_fetcher", "fast_http"))
        fetcher.currentTextChanged.connect(lambda v: self.db.set_setting("default_fetcher", v))

        timeout = QSpinBox()
        timeout.setRange(1, 300)
        timeout.setValue(self.db.get_setting("default_timeout", 30))
        timeout.valueChanged.connect(lambda v: self.db.set_setting("default_timeout", v))

        concurrency = QSpinBox()
        concurrency.setRange(1, 64)
        concurrency.setValue(self.db.get_setting("default_concurrency", 4))
        concurrency.valueChanged.connect(lambda v: self.db.set_setting("default_concurrency", v))

        delay = QSpinBox()
        delay.setRange(0, 60000)
        delay.setValue(self.db.get_setting("default_delay_ms", 0))
        delay.valueChanged.connect(lambda v: self.db.set_setting("default_delay_ms", v))

        form.addRow("Default fetcher", fetcher)
        form.addRow("Default timeout (s)", timeout)
        form.addRow("Default concurrency", concurrency)
        form.addRow("Default delay (ms)", delay)
        return w

    def _browser_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        status = QLabel("Checking Scrapling / browser status...")
        layout.addWidget(status)
        if engine.SCRAPLING_AVAILABLE:
            status.setText("✓ Scrapling is installed and importable.")
        else:
            status.setText(f"✗ Scrapling is not available: {engine.SCRAPLING_IMPORT_ERROR}")
        reinstall_btn = QPushButton("Reinstall Browser Dependencies (scrapling install)")
        reinstall_btn.clicked.connect(self._reinstall_hint)
        layout.addWidget(reinstall_btn)
        layout.addStretch(1)
        return w

    def _storage_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        data_dir = QLabel(f"Data directory: {Path(self.db.path).resolve().parent}")
        layout.addWidget(data_dir)
        clear_cache_btn = QPushButton("Clear Cache")
        clear_cache_btn.clicked.connect(self._clear_cache)
        layout.addWidget(clear_cache_btn)
        layout.addStretch(1)
        return w

    def _advanced_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        debug = QCheckBox("Debug mode")
        debug.setChecked(self.db.get_setting("debug_mode", False))
        debug.toggled.connect(lambda v: self.db.set_setting("debug_mode", v))
        level = QComboBox()
        level.addItems(["INFO", "DEBUG"])
        level.setCurrentText(self.db.get_setting("logging_level", "INFO"))
        level.currentTextChanged.connect(lambda v: self.db.set_setting("logging_level", v))
        form.addRow(debug)
        form.addRow("Logging level", level)
        return w

    def _pick_folder(self, line_edit: QLineEdit):
        folder = QFileDialog.getExistingDirectory(self, "Choose export folder", line_edit.text())
        if folder:
            line_edit.setText(folder)

    def _reinstall_hint(self):
        QMessageBox.information(
            self, "Browser dependencies",
            "LOGY لا يشغّل أوامر تثبيت من جوه الواجهة تلقائيًا. "
            "افتح الطرفية وشغّل:\n\npip install scrapling\nscrapling install",
        )

    def _clear_cache(self):
        cache_dir = Path(self.db.path).resolve().parent / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        QMessageBox.information(self, "Cache", "تم مسح الكاش.")
