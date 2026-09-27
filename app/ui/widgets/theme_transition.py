"""Full-window iris reveal used when LOGY switches between day and night."""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QBrush, QColor, QConicalGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class ThemeTransitionOverlay(QWidget):
    """Keep the previous palette visible while a luminous iris reveals the new one."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._snapshot = QPixmap()
        self._origin = QPointF()
        self._progress = 0.0
        self._light = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(920)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self._finish)
        parent.installEventFilter(self)
        self.hide()

    def capture(self) -> None:
        """Save the current central widget before its palette is changed."""
        parent = self.parentWidget()
        if parent is not None:
            self._snapshot = parent.grab()

    def start(self, origin: QPointF, light: bool) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        if self._snapshot.isNull():
            self._snapshot = parent.grab()
        self._origin = QPointF(origin)
        self._light = light
        self._progress = 0.0
        self._anim.stop()
        self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.repaint()
        self._anim.start()

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(watched.rect())
            self.update()
        return super().eventFilter(watched, event)

    def _on_tick(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _finish(self) -> None:
        self.hide()
        self._snapshot = QPixmap()
        self._progress = 0.0

    def paintEvent(self, _event) -> None:
        if self._snapshot.isNull():
            return

        width, height = self.width(), self.height()
        cx = max(0.0, min(float(width), self._origin.x()))
        cy = max(0.0, min(float(height), self._origin.y()))
        farthest = max(
            math.hypot(cx, cy),
            math.hypot(width - cx, cy),
            math.hypot(cx, height - cy),
            math.hypot(width - cx, height - cy),
        )
        progress = max(0.0, min(1.0, self._progress))
        radius = progress * (farthest + 54.0)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Odd-even clipping leaves a growing transparent circle through which
        # the freshly themed widgets underneath become visible.
        reveal = QPainterPath()
        reveal.setFillRule(Qt.FillRule.OddEvenFill)
        reveal.addRect(QRectF(0.0, 0.0, width, height))
        if radius > 0.0:
            reveal.addEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))
        p.setClipPath(reveal, Qt.ClipOperation.ReplaceClip)
        p.drawPixmap(QRectF(0.0, 0.0, width, height), self._snapshot, QRectF(self._snapshot.rect()))
        p.setClipping(False)

        # A luminous, rotating edge gives the reveal a little more character
        # than a plain wipe while keeping the content readable underneath.
        fade = 1.0 - progress
        if radius > 4.0 and fade > 0.01:
            circle = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            glow_color = QColor(111, 89, 255, int(50 * fade))
            p.setPen(QPen(glow_color, 18.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(circle)

            echo_radius = radius + 34.0 * fade
            echo = QRectF(cx - echo_radius, cy - echo_radius, echo_radius * 2, echo_radius * 2)
            echo_color = QColor(65, 183, 255, int(34 * fade))
            p.setPen(QPen(echo_color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(echo)

            gradient = QConicalGradient(QPointF(cx, cy), -progress * 540.0)
            if self._light:
                colors = ((0.0, QColor("#A98BFF")), (0.36, QColor("#59C8FF")),
                          (0.68, QColor("#FFFFFF")), (1.0, QColor("#A98BFF")))
            else:
                colors = ((0.0, QColor("#C0A6FF")), (0.36, QColor("#4A9CFF")),
                          (0.68, QColor("#78F0E0")), (1.0, QColor("#C0A6FF")))
            for stop, color in colors:
                color.setAlphaF(fade)
                gradient.setColorAt(stop, color)
            p.setPen(QPen(QBrush(gradient), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawEllipse(circle)

            # Small travelling glints ride the edge like sparks from the toggle.
            for i in range(6):
                angle = -progress * math.tau * 1.6 + i * math.tau / 6.0
                drift = math.sin(progress * math.tau * 2.0 + i * 1.4) * 5.0
                px = cx + (radius + drift) * math.cos(angle)
                py = cy + (radius + drift) * math.sin(angle)
                size = 2.0 + (i % 3) * 0.7
                alpha = int(205 * fade)
                p.setPen(QPen(QColor(255, 255, 255, alpha), 1.2, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(px - size, py), QPointF(px + size, py))
                p.drawLine(QPointF(px, py - size), QPointF(px, py + size))

        p.end()
