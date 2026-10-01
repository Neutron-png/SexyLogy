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

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate

from app.core.storage.db import Database

PAGE_SIZE = 200


def quality_pill(value) -> tuple[str, str, str] | None:
    """(short label, kind) for the concept's Quality column. The stored
    digital_label strings are long ("Weak site - strong lead"); pills show
    the short concept wording so they never need clipping. Returns None
    for anything that isn't a digital_label/quality value."""
    s = str(value)
    if s.startswith("No website"):
        return "No website", "bad"
    if s.startswith("Weak site"):
        return "Strong lead", "ok"
    if s.startswith("Some gaps"):
        return "Worth a look", "warn"
    if s.startswith("Strong site"):
        return "Low priority", "dim"
    return None


class QualityPillDelegate(QStyledItemDelegate):
    """Draws digital_label values as the concept's rounded status pills,
    elided inside the cell — never overflowing into the next column."""

    def paint(self, p: QPainter, opt, index):
        text = index.data(Qt.ItemDataRole.DisplayRole)
        pill = quality_pill(text) if text else None
        if not pill:
            super().paint(p, opt, index)
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        from app.ui import theme as ui_theme
        tk = ui_theme.tokens()
        kind = pill[1]
        if kind == "ok":
            bg, fg, ln = QColor(tk["SUCCESS"]), QColor(tk["SUCCESS"]), QColor(tk["OK_LINE"])
        elif kind == "warn":
            bg, fg, ln = QColor(tk["WARNING"]), QColor(tk["WARNING"]), QColor(tk["WARNING"])
        elif kind == "bad":
            bg, fg, ln = QColor(tk["DANGER"]), QColor(tk["BAD_TEXT"]), QColor(tk["DANGER"])
        else:  # dim — tokens hold CSS rgba() strings QColor can't parse, build manually
            dark = ui_theme.current() == "dark"
            bg = QColor(255, 255, 255, 28) if dark else QColor(20, 26, 46, 26)
            fg = QColor("#8FA3C0") if dark else QColor("#5A6073")
            ln = QColor(255, 255, 255, 30) if dark else QColor(20, 26, 46, 30)
        bg.setAlphaF(0.12 if kind != "dim" else bg.alphaF())

        r = opt.rect
        f = p.font(); f.setPointSizeF(8.0); f.setBold(True); p.setFont(f)
        avail = max(0, r.width() - 16)
        label = p.fontMetrics().elidedText(pill[0], Qt.TextElideMode.ElideRight, avail)
        w = min(avail + 16, r.width() - 4)
        h = 20
        y = r.y() + (r.height() - h) // 2
        x = r.x() + 4
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(x, y, w, h), h / 2.0, h / 2.0)
        p.setPen(QPen(ln, 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, h - 1), (h - 1) / 2.0, (h - 1) / 2.0)
        p.setPen(fg)
        p.drawText(QRectF(x, y, w, h), Qt.AlignmentFlag.AlignCenter, label)
        p.restore()


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
            try:
                data = json.loads(row["data_json"])
            except (TypeError, ValueError):
                # corrupted row (crash mid-write / manual edit): degrade
                # to an empty record instead of crashing the whole table
                # (audit QA BUG-005) - history.py guards the same parse.
                data = {}
            for k in data.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        self._columns = cols or ["value"]

    def append_live_result(self):
        """Called after a new result is persisted mid-job. Fast path: when
        the whole result set is already loaded and columns are stable, an
        O(1) row insert - the old full beginResetModel() per lead made the
        table flash and re-query on every single saved lead (audit M)."""
        new_total = self.db.count_results(self.job_id)
        if (new_total == self._total + 1 and self._columns
                and len(self._loaded_rows) == self._total):
            rows = self.db.page_results(self.job_id, new_total - 1, 1)
            if rows:
                import json
                try:
                    data = json.loads(rows[0]["data_json"])
                except (TypeError, ValueError):
                    data = {}  # corrupted row: show empty cells, never crash (audit QA BUG-005)
                data["_source_url"] = rows[0]["source_url"]
                data["_scraped_at"] = rows[0]["scraped_at"]
                data["_id"] = rows[0]["id"]
                new_cols = [k for k in data.keys() if k not in self._columns]
                if not new_cols:
                    self.beginInsertRows(QModelIndex(), self._total, self._total)
                    self._loaded_rows.append(data)
                    self._total = new_total
                    self.endInsertRows()
                    return
        # columns changed / gap: full refresh is the only correct move
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
            try:
                data = json.loads(row["data_json"])
            except (TypeError, ValueError):
                data = {}  # corrupted row: show empty cells, never crash (audit QA BUG-005)
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
