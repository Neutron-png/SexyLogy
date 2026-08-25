"""
Virtualized results table (spec section 14: "must handle large datasets
efficiently... do not render thousands of rows directly at once").

Backed by a QAbstractTableModel that only ever holds one small window of
rows in memory, fetched from SQLite via Database.page_results(). Qt's
QTableView asks the model for rows lazily as the user scrolls, and
fetchMore()/canFetchMore() grow the visible row count incrementally.
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from app.core.storage.db import Database

PAGE_SIZE = 200


class ResultsTableModel(QAbstractTableModel):
    def __init__(self, db: Database, job_id: int, parent=None):
        super().__init__(parent)
        self.db = db
        self.job_id = job_id
        self._columns: list[str] = []
        self._loaded_rows: list[dict] = []
        self._total = 0
        self.refresh_columns_and_count()

    # --- data source management ---
    def refresh_columns_and_count(self):
        self._total = self.db.count_results(self.job_id)
        sample = self.db.page_results(self.job_id, 0, 20)
        cols: list[str] = []
        seen = set()
        for row in sample:
            import json
            data = json.loads(row["data_json"])
            for k in data.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        self._columns = cols or ["value"]

    def append_live_result(self):
        """Called after a new result is persisted mid-job; keeps row count in sync."""
        self.beginResetModel()
        self.refresh_columns_and_count()
        self._loaded_rows = []
        self.endResetModel()

    # --- Qt model interface ---
    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._loaded_rows) if not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns) if not parent.isValid() else 0

    def canFetchMore(self, parent=QModelIndex()) -> bool:
        return len(self._loaded_rows) < self._total

    def fetchMore(self, parent=QModelIndex()) -> None:
        import json
        offset = len(self._loaded_rows)
        batch = self.db.page_results(self.job_id, offset, PAGE_SIZE)
        if not batch:
            return
        self.beginInsertRows(QModelIndex(), offset, offset + len(batch) - 1)
        for row in batch:
            data = json.loads(row["data_json"])
            data["_source_url"] = row["source_url"]
            data["_scraped_at"] = row["scraped_at"]
            data["_id"] = row["id"]
            self._loaded_rows.append(data)
        self.endInsertRows()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        row = self._loaded_rows[index.row()]
        col_name = self._columns[index.column()]
        value = row.get(col_name)
        if role == Qt.ItemDataRole.DisplayRole:
            if value in (None, ""):
                return ""  # empty-value highlighting handled by delegate, see below
            if isinstance(value, (list, dict)):
                import json
                return json.dumps(value, ensure_ascii=False)
            return str(value)
        if role == Qt.ItemDataRole.BackgroundRole and (value is None or value == ""):
            from PySide6.QtGui import QColor
            return QColor(239, 68, 68, 25)  # subtle red tint for empty values
        return None

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._columns[section]
        return str(section + 1)

    def row_dict(self, row: int) -> Optional[dict]:
        if 0 <= row < len(self._loaded_rows):
            return self._loaded_rows[row]
        return None

    @property
    def total_count(self) -> int:
        return self._total
