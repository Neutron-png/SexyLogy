"""
Add/Edit Source dialog - "خليني اقدر من جوا اضيف مصادر جديدة".

Lets the user define a new scraping source (a domain + a "repeat over"
container selector + the fields to pull from each listing + an optional
second-fetch detail_config) entirely from inside the app, the same shape
app/core/engine/builtin_templates.py's SOURCE_PROFILES already uses for
yellowpages/yelp/thumbtack. Saved sources are persisted via
Database.create_custom_source()/update_custom_source() and merged into
every "All Sources" run by builtin_templates.get_all_source_profiles(),
so a source added here shows up in "Load All Sources (combined)" and
Quick Start's "All Sources" niche option with no other setup.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QPushButton, QLabel, QMessageBox,
)

from app.core.models import ExtractionField
from app.ui.widgets.field_builder import FieldBuilder


class SourceDialog(QDialog):
    def __init__(self, parent=None, existing: dict | None = None):
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle("Edit Source" if existing else "Add Source")
        self.setMinimumWidth(600)
        self.setMinimumHeight(480)

        layout = QVBoxLayout(self)

        note = QLabel(
            "Define a new site the same way LOGY already knows yellowpages.com/yelp.com: a "
            "domain to match, a 'repeat over' selector for one listing card, and the fields "
            "to pull from it. Use Inspect Element on a real page from this site first."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("e.g. thumbtack")
        self.domain_input = QLineEdit()
        self.domain_input.setPlaceholderText("e.g. thumbtack.com (matched against each fetched URL's host)")
        self.container_input = QLineEdit()
        self.container_input.setPlaceholderText("CSS/XPath selector matching one listing card, e.g. .result")
        self.container_type_combo = QComboBox()
        self.container_type_combo.addItems(["css", "xpath"])
        form.addRow("Name", self.name_input)
        form.addRow("Domain", self.domain_input)
        form.addRow("Repeat over (container)", self.container_input)
        form.addRow("Container type", self.container_type_combo)
        layout.addLayout(form)

        layout.addWidget(QLabel("Fields to extract from each listing:"))
        self.field_builder = FieldBuilder()
        layout.addWidget(self.field_builder, 1)

        detail_note = QLabel(
            "Optional: if a field (e.g. phone) only appears on the listing's OWN page, not the "
            "search-results page, set a link field (must match a field name above) + a regex to "
            "pull the phone from that second page - see yelp's own setup for the same pattern."
        )
        detail_note.setWordWrap(True)
        detail_note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(detail_note)

        detail_row = QFormLayout()
        self.detail_link_field_input = QLineEdit()
        self.detail_link_field_input.setPlaceholderText("e.g. profile_url")
        self.detail_regex_input = QLineEdit()
        self.detail_regex_input.setPlaceholderText(r"e.g. \(\d{3}\)\s?\d{3}-\d{4}")
        detail_row.addRow("Link field (optional)", self.detail_link_field_input)
        detail_row.addRow("Phone regex (optional)", self.detail_regex_input)
        layout.addLayout(detail_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("Save Source")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(save_btn)
        layout.addLayout(buttons)

        # Result attributes, set only on a successful _save() -> accept().
        self.result_name: str = ""
        self.result_domain: str = ""
        self.result_container: dict = {}
        self.result_fields: list[dict] = []
        self.result_detail_config: dict | None = None

        if existing:
            self._load(existing)

    def _load(self, existing: dict):
        self.name_input.setText(existing.get("name", ""))
        self.domain_input.setText(existing.get("domain", ""))
        container = existing.get("container") or {}
        self.container_input.setText(container.get("selector", ""))
        idx = self.container_type_combo.findText(container.get("type", "css"))
        if idx >= 0:
            self.container_type_combo.setCurrentIndex(idx)
        fields = existing.get("fields") or []
        as_objs = [f if isinstance(f, ExtractionField) else ExtractionField.from_dict(f) for f in fields]
        self.field_builder.load_fields(as_objs)
        detail = existing.get("detail_config") or {}
        self.detail_link_field_input.setText(detail.get("link_field", ""))
        regex_fields = detail.get("regex_fields") or {}
        self.detail_regex_input.setText(regex_fields.get("phone", ""))

    def _save(self):
        name = self.name_input.text().strip()
        domain = self.domain_input.text().strip().lower()
        container_selector = self.container_input.text().strip()
        fields = self.field_builder.get_fields()

        if not name or not domain or not container_selector or not fields:
            QMessageBox.warning(
                self, "Add Source",
                "لازم تملأ: الاسم، الدومين، الـ container selector (repeat over)، وحقل واحد على الأقل.",
            )
            return

        self.result_name = name
        self.result_domain = domain
        self.result_container = {"selector": container_selector, "type": self.container_type_combo.currentText()}
        self.result_fields = [f.to_dict() for f in fields]

        link_field = self.detail_link_field_input.text().strip()
        phone_regex = self.detail_regex_input.text().strip()
        self.result_detail_config = (
            {"link_field": link_field, "fields": [], "regex_fields": {"phone": phone_regex}}
            if link_field and phone_regex else None
        )
        self.accept()
