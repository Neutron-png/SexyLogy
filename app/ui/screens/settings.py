from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QFormLayout, QFileDialog, QMessageBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from app.core.storage.db import Database
from app.core.engine import fetch_engine as engine
from app.core.exports.exporter import extra_fields_from_settings


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
        tabs.addTab(self._search_tab(), "Search")
        tabs.addTab(self._odoo_export_tab(), "Odoo Export")
        tabs.addTab(self._browser_tab(), "Browser")
        tabs.addTab(self._storage_tab(), "Storage")
        tabs.addTab(self._advanced_tab(), "Advanced")
        layout.addWidget(tabs, 1)

    def _search_tab(self) -> QWidget:
        """LOGY Search (app/core/search) provider settings - the
        zero-cost internal SERP layer. Persisted under 'search_config';
        SearchService re-reads it per construction."""
        from app.core.search.service import DEFAULT_CONFIG

        w = QWidget()
        form = QFormLayout(w)
        cfg = self.db.get_setting("search_config", {}) or {}

        provider = QComboBox()
        provider.addItems(["auto", "ddg_html", "searxng"])
        provider.setCurrentText(cfg.get("primary", DEFAULT_CONFIG["primary"]))
        searxng_url = QLineEdit(cfg.get("searxng_base_url", ""))
        ttl = QSpinBox()
        ttl.setRange(60, 86400 * 7)
        ttl.setValue(int(cfg.get("cache_ttl_s", DEFAULT_CONFIG["cache_ttl_s"])))
        interval = QSpinBox()
        interval.setRange(0, 60)
        interval.setValue(int(cfg.get("min_interval_s", DEFAULT_CONFIG["min_interval_s"])))
        concurrency = QSpinBox()
        concurrency.setRange(1, 8)
        concurrency.setValue(int(cfg.get("max_concurrency", DEFAULT_CONFIG["max_concurrency"])))

        def _save():
            self.db.set_setting("search_config", {
                "primary": provider.currentText(),
                "searxng_base_url": searxng_url.text().strip(),
                "cache_ttl_s": ttl.value(),
                "min_interval_s": interval.value(),
                "max_concurrency": concurrency.value(),
            })

        provider.currentTextChanged.connect(lambda *_: _save())
        searxng_url.textChanged.connect(lambda *_: _save())
        ttl.valueChanged.connect(lambda *_: _save())
        interval.valueChanged.connect(lambda *_: _save())
        concurrency.valueChanged.connect(lambda *_: _save())

        form.addRow("Default provider", provider)
        form.addRow("SearXNG base URL (optional)", searxng_url)
        form.addRow("Search cache TTL (s)", ttl)
        form.addRow("Min interval between searches (s)", interval)
        form.addRow("Max concurrent searches", concurrency)
        note = QLabel(
            "LOGY's own zero-cost search layer (no Serper/SerpAPI keys needed). "
            "'auto' uses the built-in free provider, with SearXNG as fallback "
            "when a base URL is set."
        )
        note.setWordWrap(True)
        form.addRow(note)
        return w

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        theme = QComboBox()
        theme.addItems(["Dark"])
        theme.setEnabled(False)
        language = QComboBox()
        language.addItems(["English", "Arabic"])
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

    def _odoo_export_tab(self) -> QWidget:
        # Different Odoo installs require different EXTRA fields beyond
        # the stock crm.lead import template - an admin can mark any
        # field required via Studio, a mandatory Sales Team, multi-company
        # setups, etc. LOGY can't guess any of these correctly (a wrong
        # guess corrupts the import same as a missing value), so this is
        # an open-ended table instead of one hardcoded "Channel" box:
        # add one row per field Odoo's own import error names (e.g.
        # "Missing required value for the field Channel"), with the exact
        # column name and the fixed value your instance expects. Applied
        # to every lead in an "Odoo CRM Lead template" export unless that
        # lead's own scraped data already has a matching field of its own
        # (see exporter.py's extra_fields_from_settings() / DEFAULT_ODOO_EXTRA_FIELDS
        # for the full precedence rules and a list of commonly-required ones).
        w = QWidget()
        layout = QVBoxLayout(w)
        note = QLabel(
            "Any required field your Odoo needs that isn't in the standard template — like Channel, "
            "Source or Sales Team — add it here: the column name exactly as it appears in Odoo's error "
            "(e.g. 'Missing required value for the field Channel'), and the fixed value to set for "
            "every lead. If the lead itself carries a field with that name, its own value wins."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        self._odoo_fields_state: list[dict] = extra_fields_from_settings(self.db.get_setting)

        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels(["Odoo Column", "Fixed Value"])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        self._odoo_fields_table = table
        layout.addWidget(table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ Add Field")
        add_btn.clicked.connect(self._add_odoo_field_row)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_odoo_field_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        layout.addStretch(1)

        self._render_odoo_fields_table()
        table.itemChanged.connect(self._save_odoo_fields_table)
        return w

    def _render_odoo_fields_table(self):
        table = self._odoo_fields_table
        table.blockSignals(True)
        table.setRowCount(len(self._odoo_fields_state))
        for row, field in enumerate(self._odoo_fields_state):
            table.setItem(row, 0, QTableWidgetItem(str(field.get("column", ""))))
            table.setItem(row, 1, QTableWidgetItem(str(field.get("value", ""))))
        table.blockSignals(False)

    def _add_odoo_field_row(self):
        self._odoo_fields_state.append({"column": "", "value": "", "aliases": None})
        self._render_odoo_fields_table()

    def _remove_odoo_field_row(self):
        rows = sorted({i.row() for i in self._odoo_fields_table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        for r in rows:
            if 0 <= r < len(self._odoo_fields_state):
                del self._odoo_fields_state[r]
        self._render_odoo_fields_table()
        self._save_odoo_fields_table()

    def _save_odoo_fields_table(self, *_args):
        table = self._odoo_fields_table
        for row in range(min(table.rowCount(), len(self._odoo_fields_state))):
            col_item = table.item(row, 0)
            val_item = table.item(row, 1)
            self._odoo_fields_state[row]["column"] = (col_item.text().strip() if col_item else "")
            self._odoo_fields_state[row]["value"] = (val_item.text() if val_item else "")
        # Rows with no column name yet (e.g. right after "+ Add Field",
        # before the user types one) are dropped rather than persisted -
        # an empty-string Odoo column header would corrupt the export.
        cleaned = [f for f in self._odoo_fields_state if f.get("column")]
        self.db.set_setting("odoo_extra_fields", cleaned)

    def _browser_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        status = QLabel("Checking the fetch engine / browser status...")
        layout.addWidget(status)
        if engine.ENGINE_AVAILABLE:
            status.setText("✓ the fetch engine is installed and importable.")
        else:
            status.setText(f"✗ the fetch engine is not available: {engine.ENGINE_IMPORT_ERROR}")
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
            "LOGY never runs install commands from the UI automatically. "
            "Open a terminal and run:\n\npip install scrapling\nscrapling install",
        )

    def _clear_cache(self):
        cache_dir = Path(self.db.path).resolve().parent / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)
        QMessageBox.information(self, "Cache", "Cache cleared.")
