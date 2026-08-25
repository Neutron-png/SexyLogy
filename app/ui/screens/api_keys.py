from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QListWidget,
    QListWidgetItem, QMessageBox, QComboBox,
)

from app.core.storage.db import Database
from app.core.storage.secrets import SecretStore

# Must match the provider ids used by app/core/engine/ai_extractor.py and
# the AI Auto-Extract tab in New Scrape - the job manager looks a key up
# by exactly this name, so free-typing a provider name here (the old
# behavior) meant the AI feature could never find it. A fixed dropdown
# removes that whole class of typo.
PROVIDERS = [("Anthropic (Claude)", "anthropic"), ("OpenAI", "openai")]


class ApiKeysScreen(QWidget):
    """Stores API keys for the optional AI Auto-Extract feature. Values
    are encrypted at rest via SecretStore and only ever shown redacted."""

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self.db = db
        self.secrets = SecretStore()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        title = QLabel("API Keys")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Used by AI Auto-Extract in New Scrape. Encrypted at rest, never written to logs or exports."
        )
        subtitle.setObjectName("pageSubtitle")
        layout.addWidget(subtitle)

        # ---- inline add form (no popup dialogs to hunt for) ----
        form_card = QWidget()
        form_card.setObjectName("card")
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(16, 14, 16, 16)
        form_layout.setSpacing(10)

        form_title = QLabel("Add / Update a key")
        form_title.setObjectName("sectionTitle")
        form_layout.addWidget(form_title)

        form_row = QHBoxLayout()
        self.provider_combo = QComboBox()
        for label, provider_id in PROVIDERS:
            self.provider_combo.addItem(label, provider_id)
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Paste your API key here (sk-... / sk-ant-...)")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_key_chk = QPushButton("👁")
        self.show_key_chk.setCheckable(True)
        self.show_key_chk.setFixedWidth(36)
        self.show_key_chk.toggled.connect(self._toggle_visibility)
        save_btn = QPushButton("Save Key")
        save_btn.setObjectName("primaryButton")
        save_btn.clicked.connect(self._save_key)

        form_row.addWidget(self.provider_combo)
        form_row.addWidget(self.key_input, 1)
        form_row.addWidget(self.show_key_chk)
        form_row.addWidget(save_btn)
        form_layout.addLayout(form_row)

        hint = QLabel(
            "Anthropic keys: console.anthropic.com/settings/keys  ·  OpenAI keys: platform.openai.com/api-keys"
        )
        hint.setStyleSheet("color: #8B95A7; font-size: 11px;")
        form_layout.addWidget(hint)

        layout.addWidget(form_card)

        saved_title = QLabel("Saved keys")
        saved_title.setObjectName("sectionTitle")
        layout.addWidget(saved_title)

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        remove_btn = QPushButton("Remove Selected")
        remove_btn.setObjectName("dangerButton")
        remove_btn.clicked.connect(self._remove_key)
        layout.addWidget(remove_btn)

        self.refresh()

    def _toggle_visibility(self, checked: bool):
        self.key_input.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def refresh(self):
        self.list_widget.clear()
        keys = self.db.get_setting("api_keys", {})
        provider_labels = dict(PROVIDERS)
        label_by_id = {pid: label for label, pid in PROVIDERS}
        if not keys:
            self.list_widget.addItem("No API keys saved yet.")
            return
        for provider_id in keys:
            label = label_by_id.get(provider_id, provider_id)
            self.list_widget.addItem(QListWidgetItem(f"{label}  ·  ••••••••  (id: {provider_id})"))

    def _save_key(self):
        provider_id = self.provider_combo.currentData()
        value = self.key_input.text().strip()
        if not value:
            QMessageBox.warning(self, "API Keys", "الصق المفتاح الأول قبل ما تحفظ.")
            return
        # The provider dropdown defaults to "Anthropic (Claude)" (added
        # first) - pasting an OpenAI key without noticing/changing it
        # files that key under "anthropic" instead, and every screen that
        # later looks up "openai" (New Scrape's AI Auto-Extract / Look up
        # owner contact info) reports "no key found" even though one
        # really is saved, just under the other provider's slot. Anthropic
        # keys start with "sk-ant-", OpenAI keys never do - a clear
        # mismatch there is caught here BEFORE saving, rather than
        # surfacing later as a confusing missing-key error somewhere else.
        looks_like_anthropic = value.startswith("sk-ant-")
        if provider_id == "openai" and looks_like_anthropic:
            if QMessageBox.question(
                self, "API Keys",
                "المفتاح ده شكله مفتاح Anthropic (بيبدأ بـ sk-ant-)، بس انت مختار 'OpenAI' في القائمة "
                "فوق. لو تكمل حفظ كده، المفتاح هيتسجل باسم 'openai' وممكن يفشل لما يتستخدم فعليًا.\n\n"
                "متأكد إنك عايز تكمل؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        elif provider_id == "anthropic" and not looks_like_anthropic and value.startswith("sk-"):
            if QMessageBox.question(
                self, "API Keys",
                "المفتاح ده شكله مفتاح OpenAI (بيبدأ بـ sk- بس مش sk-ant-)، بس انت مختار 'Anthropic' في "
                "القائمة فوق. لو تكمل حفظ كده، المفتاح هيتسجل باسم 'anthropic' وممكن يفشل لما يتستخدم "
                "فعليًا.\n\nمتأكد إنك عايز تكمل؟",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        keys = self.db.get_setting("api_keys", {})
        keys[provider_id] = self.secrets.encrypt(value)
        self.db.set_setting("api_keys", keys)
        self.key_input.clear()
        self.refresh()
        QMessageBox.information(self, "API Keys", "تم الحفظ. المفتاح مشفّر على القرص.")

    def _remove_key(self):
        item = self.list_widget.currentItem()
        if not item or "id: " not in item.text():
            return
        provider_id = item.text().split("id: ")[-1].rstrip(")")
        keys = self.db.get_setting("api_keys", {})
        keys.pop(provider_id, None)
        self.db.set_setting("api_keys", keys)
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
