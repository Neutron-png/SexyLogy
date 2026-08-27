"""
City picker dialog - "خليني احدد المدن ... تبقى في سلايدر فيه كل المدن
اللي بنشتغل عليها + سيرش بار فيها".

Replaces free-text city entry with a scrollable, checkable list of every
city in CITY_POOL plus a live search box to filter it, so restricting a
run to specific cities (see builtin_templates.generate_niche_urls() and
friends) is pick-from-a-list instead of type-it-yourself. "Select All" /
"Clear All" make the common cases (everything, or start from nothing and
hand-pick a few) one click instead of N.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QLabel,
)

from app.core.engine.builtin_templates import CITY_POOL


class CityPickerDialog(QDialog):
    def __init__(self, parent=None, selected: list[tuple[str, str]] | None = None):
        super().__init__(parent)
        self.setWindowTitle("Choose Cities")
        self.setMinimumWidth(420)
        self.setMinimumHeight(520)

        # None/omitted -> everything checked (the previous, only, behavior:
        # the full top-100 CITY_POOL).
        selected_set = set(selected) if selected is not None else set(CITY_POOL)

        layout = QVBoxLayout(self)

        note = QLabel(f"{len(CITY_POOL)} cities LOGY ships with (US + Canada, population 100k+).")
        note.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(note)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search cities...")
        self.search_input.textChanged.connect(self._filter)
        layout.addWidget(self.search_input)

        self.list_widget = QListWidget()
        for city, state in CITY_POOL:
            item = QListWidgetItem(f"{city}, {state}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if (city, state) in selected_set else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, (city, state))
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #8B95A7; font-size: 11px;")
        layout.addWidget(self.count_label)
        self.list_widget.itemChanged.connect(self._update_count)
        self._update_count()

        quick_row = QHBoxLayout()
        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        clear_all_btn = QPushButton("Clear All")
        clear_all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        quick_row.addWidget(select_all_btn)
        quick_row.addWidget(clear_all_btn)
        quick_row.addStretch(1)
        layout.addLayout(quick_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setObjectName("primaryButton")
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

    def _filter(self, text: str):
        text = text.strip().lower()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _set_all(self, state: Qt.CheckState):
        # Only affects currently VISIBLE (i.e. search-filtered) rows, so
        # "Clear All" after searching "Texas" only clears Texas cities,
        # not the user's picks elsewhere - matches how a filtered
        # multi-select list is expected to behave.
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if not item.isHidden():
                item.setCheckState(state)

    def _update_count(self, *_args):
        checked = sum(
            1 for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == Qt.CheckState.Checked
        )
        total = self.list_widget.count()
        self.count_label.setText(
            f"All {total} cities selected" if checked == total else f"{checked} of {total} cities selected"
        )

    def selected_cities(self) -> list[tuple[str, str]]:
        return [
            self.list_widget.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.list_widget.count())
            if self.list_widget.item(row).checkState() == Qt.CheckState.Checked
        ]
