from __future__ import annotations

from PySide6.QtWidgets import QPlainTextEdit
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor

LEVEL_COLORS = {
    "INFO": "#8B95A7",
    "SUCCESS": "#22C55E",
    "WARNING": "#F59E0B",
    "ERROR": "#EF4444",
    "DEBUG": "#8B5CF6",
}


class LogPanel(QPlainTextEdit):
    """Append-only live log view (spec section 13: 'show live logs')."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)  # cap memory for very long runs
        self.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")

    def append_entry(self, level: str, message: str):
        color = LEVEL_COLORS.get(level, "#E5E9F0")
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        symbol = {"SUCCESS": "✓", "ERROR": "✗", "WARNING": "⚠", "INFO": "•", "DEBUG": "…"}.get(level, "•")
        cursor.insertText(f"{symbol} [{level}] {message}\n")
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
