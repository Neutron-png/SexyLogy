"""Activity feed strip (concept-17 'trading desk' feed): one line per event
with a glowing status dot, plain message and right-aligned detail.

The run panel in New Campaign uses ActivityFeed; the full Logs screen keeps
the monospace LogPanel. Both consume the same (level, message) pairs."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget, QLabel, QFrame, QHBoxLayout,
)
from PySide6.QtGui import QTextCharFormat, QColor, QTextCursor
from PySide6.QtCore import Qt

from app.ui import theme as ui_theme


def _colors() -> dict:
    dark = ui_theme.current() == "dark"
    return {
        "INFO": "#8FA3C0" if dark else "#5A6073",
        "SUCCESS": "#2EE6A8" if dark else "#0E9F6E",
        "WARNING": "#FFC24B" if dark else "#B45309",
        "ERROR": "#FF6B6B" if dark else "#DC2626",
        "DEBUG": "#A78BFA" if dark else "#7C5CE0",
    }


_DOT = {
    "SUCCESS": "ok",
    "INFO": "ok",
    "PROGRESS": "live",
    "WARNING": "warn",
    "ERROR": "bad",
    "DEBUG": "dim",
}
_DETAIL = {
    "SUCCESS": "done",
    "ERROR": "failed",
    "WARNING": "recovered ✓",
    "PROGRESS": "in progress…",
}


class _FeedLine(QFrame):
    def __init__(self, level: str, message: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(9)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        kind = _DOT.get(level, "ok")
        dot.setObjectName(f"feedDot{kind.capitalize()}")
        lay.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        txt = QLabel(message)
        txt.setObjectName("feedText")
        lay.addWidget(txt, 1)
        detail = _DETAIL.get(level, "")
        if detail:
            d = QLabel(detail)
            d.setObjectName("feedDetail")
            lay.addWidget(d, 0, Qt.AlignmentFlag.AlignVCenter)


class ActivityFeed(QScrollArea):
    """Compact live feed: latest events at the bottom, auto-scrolls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._host = QWidget()
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(1)
        self._lay.addStretch(1)
        self.setWidget(self._host)

    def append_entry(self, level: str, message: str):
        line = _FeedLine(level, message)
        self._lay.insertWidget(self._lay.count() - 1, line)
        if self._lay.count() > 61:  # cap like LogPanel's block count
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear(self):
        while self._lay.count() > 1:
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()


class LogPanel(QPlainTextEdit):
    """Append-only live log view (spec section 13: 'show live logs')."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(5000)  # cap memory for very long runs
        self.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 12px;")

    def append_entry(self, level: str, message: str):
        color = _colors().get(level, ui_theme.tokens()["TEXT"])
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        symbol = {"SUCCESS": "✓", "ERROR": "×", "WARNING": "▲", "INFO": "•", "DEBUG": "·"}.get(level, "•")
        cursor.insertText(f"{symbol} [{level}] {message}\n")
        self.setTextCursor(cursor)
        self.ensureCursorVisible()