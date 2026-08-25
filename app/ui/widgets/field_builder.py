"""
Visual Field Builder (spec section 8): add / edit / reorder / delete
extraction fields without writing code. Drag-and-drop reordering is
provided natively by QTableWidget's internal move mode.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton,
    QComboBox, QLineEdit, QHeaderView, QAbstractItemView,
)
from PySide6.QtCore import Qt

from app.core.models import ExtractionField, ExtractionType

COLUMNS = ["Field Name", "Selector", "Type", "Extract As", "Attribute", "Multiple"]


class FieldBuilder(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Field")
        self.add_btn.setObjectName("primaryButton")
        self.add_btn.clicked.connect(self.add_row)
        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.duplicate_btn = QPushButton("Duplicate")
        self.duplicate_btn.clicked.connect(self.duplicate_selected)
        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.duplicate_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.table.setDragDropOverwriteMode(False)
        layout.addWidget(self.table)

    def add_row(self, field: ExtractionField | None = None):
        field = field or ExtractionField(name=f"field_{self.table.rowCount() + 1}", selector="", extraction_type=ExtractionType.TEXT)
        row = self.table.rowCount()
        self.table.insertRow(row)

        self.table.setItem(row, 0, QTableWidgetItem(field.name))
        self.table.setItem(row, 1, QTableWidgetItem(field.selector))

        selector_type = QComboBox()
        selector_type.addItems(["css", "xpath"])
        selector_type.setCurrentText(field.selector_type)
        self.table.setCellWidget(row, 2, selector_type)

        extraction_type = QComboBox()
        extraction_type.addItems([t.value for t in ExtractionType])
        extraction_type.setCurrentText(field.extraction_type.value)
        self.table.setCellWidget(row, 3, extraction_type)

        self.table.setItem(row, 4, QTableWidgetItem(field.attribute or ""))

        multiple = QComboBox()
        multiple.addItems(["no", "yes"])
        multiple.setCurrentText("yes" if field.multiple else "no")
        self.table.setCellWidget(row, 5, multiple)

    def delete_selected(self):
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def duplicate_selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        for row in rows:
            self.add_row(self.get_fields()[row])

    def get_fields(self) -> list[ExtractionField]:
        fields = []
        for row in range(self.table.rowCount()):
            name = self.table.item(row, 0).text() if self.table.item(row, 0) else ""
            selector = self.table.item(row, 1).text() if self.table.item(row, 1) else ""
            selector_type = self.table.cellWidget(row, 2).currentText()
            extraction_type = ExtractionType(self.table.cellWidget(row, 3).currentText())
            attribute = self.table.item(row, 4).text() if self.table.item(row, 4) else ""
            multiple = self.table.cellWidget(row, 5).currentText() == "yes"
            fields.append(ExtractionField(
                name=name, selector=selector, selector_type=selector_type,
                extraction_type=extraction_type, attribute=attribute or None, multiple=multiple,
            ))
        return fields

    def load_fields(self, fields: list[ExtractionField]):
        self.table.setRowCount(0)
        for f in fields:
            self.add_row(f)
