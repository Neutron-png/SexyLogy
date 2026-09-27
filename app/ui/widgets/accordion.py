"""
Accordion rows (macOS-Settings style): icon + title + live summary + chevron;
content expands with an animated height reveal. One row open at a time.
"""
from __future__ import annotations

from app.ui import theme as ui_theme
from app.ui.widgets import icons
from PySide6.QtCore import QEasingCurve, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class IconChip(QWidget):
    """Tiny painted icon (bars / arrow / table / gear) — no font glyphs."""

    def __init__(self, kind: str):
        super().__init__()
        self._kind = kind
        self.setFixedSize(30, 30)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tk = ui_theme.tokens()
        dark = ui_theme.current() == "dark"
        chip = QColor(255, 255, 255, 26) if dark else QColor(255, 255, 255, 165)
        line = QColor(255, 255, 255, 26) if dark else QColor(255, 255, 255, 190)
        p.setPen(QPen(line, 1))
        p.setBrush(chip)
        p.drawEllipse(QRectF(0.5, 0.5, 29, 29))
        ic_hex = "#B7C4DB" if dark else "#5A6073"
        pm = icons.pixmap(self._kind, ic_hex, 17)
        p.drawPixmap(QRectF(6.5, 6.5, 17, 17).toRect(), pm)


def _tk():
    return None


_MAX = 16777215


class _Head(QFrame):
    clicked = Signal()

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)


class Accordion(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(10)
        self._rows = []
        self._by_key: dict[str, dict] = {}

    def add_row(self, key: str, icon: str, title: str, subtitle: str, summary: str, content: QWidget, expanded: bool = False):
        card = QFrame()
        card.setObjectName("accCard")
        v = QVBoxLayout(card)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = _Head()
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        h = QHBoxLayout(head)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        ic = IconChip(icon)
        h.addWidget(ic)

        tt = QVBoxLayout()
        tt.setSpacing(1)
        b = QLabel(title)
        b.setObjectName("accTitle")
        s = QLabel(subtitle)
        s.setObjectName("accSub")
        tt.addWidget(b)
        tt.addWidget(s)
        h.addLayout(tt)
        h.addStretch(1)

        sm = QLabel(summary)
        sm.setObjectName("accSum")
        h.addWidget(sm)

        chev = QLabel("›" if not expanded else "⌄")
        chev.setObjectName("accChev")
        h.addWidget(chev)
        v.addWidget(head)

        body = QWidget()
        body.setObjectName("accBody")
        bv = QVBoxLayout(body)
        bv.setContentsMargins(14, 0, 14, 14)
        bv.addWidget(content)
        body.setVisible(expanded)
        body.setMaximumHeight(0 if not expanded else _MAX)
        v.addWidget(body)

        anim = QVariantAnimation(self)
        anim.setDuration(250)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def on_val(v):
            body.setMaximumHeight(int(v))

        def on_done():
            if not row.get("opening", False):
                body.setVisible(False)
            else:
                body.setMaximumHeight(_MAX)

        anim.valueChanged.connect(on_val)
        anim.finished.connect(on_done)

        row = dict(head=head, body=body, chev=chev, anim=anim, card=card, summary=sm, opening=False)
        self._rows.append(row)
        self._by_key[key] = row
        head.clicked.connect(lambda: self._toggle(row))
        self._lay.addWidget(card)

    def set_summary(self, key: str, text: str):
        row = self._by_key.get(key)
        if row:
            row["summary"].setText(text)

    def _toggle(self, row):
        body = row["body"]
        opening = not body.isVisible()
        if opening:
            for other in self._rows:
                if other is not row and other["body"].isVisible():
                    self._animate(other, opening=False)
            body.setVisible(True)
            target = body.sizeHint().height() + 20
            self._animate(row, opening=True, target=target)
            row["chev"].setText("⌄")
        else:
            self._animate(row, opening=False)
            row["chev"].setText("›")

    def _animate(self, row, opening: bool, target: int = 0):
        body, anim = row["body"], row["anim"]
        anim.stop()
        row["opening"] = opening
        start = 0 if opening else body.height()
        end = target if opening else 0
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()
