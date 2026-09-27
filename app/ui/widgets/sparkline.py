"""Live engine waveform: scrolling gradient wave + pulse dot, theme-aware."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QWidget

from app.ui import theme as ui_theme


class Sparkline(QWidget):
    """Always-moving engine pulse: two blended waves scrolling over time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(26)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        self._phase += 0.045
        self.update()

    def showEvent(self, ev):
        self._timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._timer.stop()
        super().hideEvent(ev)

    def _wave(self, x_norm: float) -> float:
        x = x_norm * 7.0
        a = math.sin(x - self._phase * 2.0)
        b = 0.55 * math.sin(x * 0.53 + self._phase * 1.3)
        c = 0.22 * math.sin(x * 2.9 + self._phase * 0.6)
        return 0.62 + 0.30 * (a + b + c) / 1.77

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        tk = ui_theme.tokens()

        step = 5
        n = max(2, w // step)
        pts = []
        for i in range(n + 1):
            fx = i / n
            y = h * self._wave(fx)
            pts.append(QPointF(fx * (w - 8) + 4, y))

        # soft area fill
        fill = QColor(tk["PRIMARY"])
        fill.setAlphaF(0.10 if ui_theme.current() == "dark" else 0.18)
        area = QPolygonF(pts + [QPointF(pts[-1].x(), h), QPointF(pts[0].x(), h)])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPolygon(area)

        # gradient stroke
        g = QLinearGradient(0, 0, w, 0)
        g.setColorAt(0.0, QColor(tk["PRIMARY"]))
        g.setColorAt(1.0, QColor(tk["PRIMARY_END"]))
        pen = QPen(g, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolyline(QPolygonF(pts))

        # pulse head with radial glow + heartbeat
        last = pts[-1]
        glow = QRadialGradient(last, 9)
        glow.setColorAt(0.0, QColor(tk["PRIMARY_END"]))
        faded = QColor(tk["PRIMARY_END"])
        faded.setAlphaF(0.0)
        glow.setColorAt(1.0, faded)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(last, 9, 9)
        p.setBrush(QColor(tk["PRIMARY_END"]))
        beat = 2.6 + 0.9 * math.sin(self._phase * 2.4)
        p.drawEllipse(last, beat, beat)
