"""
Aurora wallpaper: the central widget paints soft light blooms behind the
translucent glass surfaces (the blur-refraction feel of the design, adapted
to Qt where CSS backdrop-filter has no equivalent).
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.ui import theme as ui_theme


def _glow(p: QPainter, cx: float, cy: float, r: float, color: QColor):
    if color_alpha(color) <= 0:
        return
    g = QRadialGradient(cx, cy, r)
    c0 = QColor(color)
    c0.setAlphaF(color_alpha(color))
    c1 = QColor(color)
    c1.setAlphaF(0.0)
    g.setColorAt(0.0, c0)
    g.setColorAt(1.0, c1)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(g)
    p.drawEllipse(QPointF(cx, cy), r, r)


def color_alpha(c: QColor) -> float:
    return c.alphaF()


class AuroraBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        dark = ui_theme.current() == "dark"

        base = QLinearGradient(0, 0, 0, h)
        if dark:
            base.setColorAt(0.0, QColor("#080B14"))
            base.setColorAt(1.0, QColor("#05070D"))
        else:
            base.setColorAt(0.0, QColor("#EFF2F8"))
            base.setColorAt(1.0, QColor("#E2E7F1"))
        p.fillRect(self.rect(), base)

        def a(hexcolor: str, alpha: float) -> QColor:
            c = QColor(hexcolor)
            c.setAlphaF(alpha)
            return c

        if dark:
            _glow(p, w * 0.10, -h * 0.10, max(w, h) * 0.62, a(0x3E7BFF, 0.30))
            _glow(p, w * 0.92, -h * 0.06, max(w, h) * 0.55, a(0x966EFF, 0.22))
            _glow(p, w * 0.86, h * 1.10, max(w, h) * 0.55, a(0x2EE6A8, 0.13))
            _glow(p, w * 0.24, h * 1.12, max(w, h) * 0.48, a(0xFF9670, 0.07))
        else:
            _glow(p, w * 0.10, -h * 0.10, max(w, h) * 0.62, a(0x5A8CFF, 0.38))
            _glow(p, w * 0.92, -h * 0.06, max(w, h) * 0.55, a(0xAA82FF, 0.28))
            _glow(p, w * 0.86, h * 1.10, max(w, h) * 0.55, a(0x28CDAA, 0.20))
        p.end()
