"""
Animated controls for the aurora theme.

Switch: drop-in QCheckBox replacement with a sliding pill knob.
ThemeToggle: the day/night sky toggle (clouds + stars + sun/moon knob) wired
to the app-wide dark/light switch.
"""
from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QAbstractButton, QCheckBox

from app.ui import theme as ui_theme


class Switch(QCheckBox):
    """iOS-style animated toggle that keeps the QCheckBox API (toggled, isChecked)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._t = 1.0 if self.isChecked() else 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(190)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_tick)
        self.toggled.connect(self._on_toggled)

    def _on_tick(self, v):
        self._t = float(v)
        self.update()

    def _on_toggled(self, checked: bool):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self):
        s = super().sizeHint()
        s.setWidth(s.width() + 42)
        return s

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, ev):
        t = self._t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        track_w, track_h = 34, 19
        fm_y = (self.height() - track_h) / 2.0
        radius = track_h / 2.0

        on = QColor()
        on.setNamedColor("#3E7BFF")
        on.setAlphaF(0.28 + 0.62 * t)
        off = QColor(128, 136, 152)
        off.setAlphaF(0.38 * (1.0 - t))

        p.setPen(Qt.PenStyle.NoPen)
        if t > 0.01:
            p.setBrush(on)
            p.drawRoundedRect(QRectF(0, fm_y, track_w, track_h), radius, radius)
        if t < 0.99:
            p.setBrush(off)
            p.drawRoundedRect(QRectF(0, fm_y, track_w, track_h), radius, radius)

        d = 15.0
        x = 2.0 + t * (track_w - d - 4.0)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QRectF(x, fm_y + 2.0, d, d))

        if self.text():
            p.setPen(QColor(ui_theme.tokens()["TEXT"]))
            p.setFont(self.font())
            p.drawText(QRectF(track_w + 10, 0, self.width() - track_w - 10, self.height()),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


# ---------------------------------------------------------------- day/night
_STAR_POS = [
    (0.14, 0.30, 1.0), (0.24, 0.62, 0.6), (0.34, 0.58, 0.8), (0.47, 0.30, 0.5),
    (0.58, 0.42, 0.7), (0.66, 0.24, 0.9), (0.78, 0.34, 0.6), (0.87, 0.52, 0.8),
    (0.93, 0.28, 0.5), (0.52, 0.62, 0.45),
]
_CLOUDS = [
    (0.22, 0.62, 1.00), (0.46, 0.40, 0.80), (0.72, 0.66, 0.90),
]


class ThemeToggle(QAbstractButton):
    """Day/night sky toggle: the moon ROLLS all the way across, bounces to a
    settle as the sun rises. The app starts its iris-reveal transition at the
    same moment, so the new palette travels with the knob instead of snapping."""

    lightChanged = Signal(bool)
    transitionStarted = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(96, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._light = False
        self._t = 0.0          # raw 0..1 progress of the transition
        self._roll_from = 0.0  # roll segment the current flight animates over
        self._roll_to = 0.0
        self._twinkle = 0.0
        self._pending_emit = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(720)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self._on_anim_done)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(60)
        self._tick_timer.timeout.connect(self._amb_tick)

    def _amb_tick(self):
        self._twinkle += 0.07
        self.update()

    def _on_tick(self, v):
        self._t = float(v)
        self.update()

    def _on_anim_done(self):
        # Notify observers when the knob has settled; the app-wide reveal
        # already started with transitionStarted at the beginning of the roll.
        if self._pending_emit:
            self._pending_emit = False
            self.lightChanged.emit(self._light)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.set_light(not self._light, animate=True)
        super().mousePressEvent(ev)

    def showEvent(self, ev):
        self._tick_timer.start()
        super().showEvent(ev)

    def hideEvent(self, ev):
        self._tick_timer.stop()
        super().hideEvent(ev)

    def set_light(self, on: bool, animate: bool = True):
        self._light = bool(on)
        self.setChecked(self._light)
        target = 1.0 if on else 0.0
        self._anim.stop()
        if not animate:
            self._pending_emit = False
            self._t = target
            self._roll_from = self._roll_to = target
            self.update()
            return
        # reversing mid-flight: start from where we actually are so the roll
        # is continuous instead of restarting from an edge
        self._pending_emit = True
        self._roll_from = self._t
        self._roll_to = target
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self.transitionStarted.emit(self._light)
        self._anim.start()

    @staticmethod
    def _out_bounce(t: float) -> float:
        n1, d1 = 7.5625, 2.75
        if t < 1 / d1:
            return n1 * t * t
        if t < 2 / d1:
            t -= 1.5 / d1
            return n1 * t * t + 0.75
        if t < 2.5 / d1:
            t -= 2.25 / d1
            return n1 * t * t + 0.9375
        t -= 2.625 / d1
        return n1 * t * t + 0.984375

    def paintEvent(self, ev):
        t = max(0.0, min(1.0, self._t))
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = h / 2.0

        from_p, to_p = self._roll_from, self._roll_to
        if to_p == from_p:
            sky, roll_pos = t, t
        else:
            # progress along THIS flight (current -> target), so the bounce
            # always lands on arrival, in both dark->light and light->dark
            prog = max(0.0, min(1.0, (t - from_p) / (to_p - from_p)))
            eased = self._out_bounce(prog)
            sky = from_p + (prog * prog * (3 - 2 * prog)) * (to_p - from_p)
            roll_pos = from_p + eased * (to_p - from_p)

        # --- sky: night navy -> day blue ---
        top = _mix(QColor(0x17, 0x1F, 0x38), QColor(0x2F, 0x7C, 0xD9), sky)
        bottom = _mix(QColor(0x0E, 0x14, 0x28), QColor(0x8E, 0xC5, 0xF0), sky)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, bottom)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        # inner bevel
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(255, 255, 255, int(30 + 40 * sky)), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)

        # --- stars (night) — alpha fade + twinkle ---
        star_alpha = max(0.0, 1.0 - sky * 1.6)
        if star_alpha > 0.01:
            p.setPen(Qt.PenStyle.NoPen)
            for i, (fx, fy, s) in enumerate(_STAR_POS):
                a = star_alpha * s * (0.72 + 0.28 * math.sin(self._twinkle * 2.2 + i * 1.7))
                c = QColor(255, 255, 255)
                c.setAlphaF(min(1.0, a))
                p.setBrush(c)
                d = 1.6 + 1.6 * s
                p.drawEllipse(QPointF(w * fx, h * fy), d, d)

        # --- clouds (day) — slow drift + fade ---
        cloud_alpha = max(0.0, sky * 1.4 - 0.2)
        if cloud_alpha > 0.01:
            cloud = QColor(255, 255, 255)
            cloud.setAlphaF(min(1.0, 0.85 * cloud_alpha))
            p.setBrush(cloud)
            for fx, fy, s in _CLOUDS:
                drift = math.sin(self._twinkle * 0.6 + fx * 6.0) * 4.0
                cx, cy = w * fx + (1 - sky) * 24 + drift, h * fy
                p.drawEllipse(QPointF(cx, cy), 13 * s, 8 * s)
                p.drawEllipse(QPointF(cx + 10 * s, cy + 2 * s), 9 * s, 6 * s)
                p.drawEllipse(QPointF(cx - 10 * s, cy + 2 * s), 8 * s, 5.5 * s)

        # --- knob: one full ROLL across the track, bounce-settling on arrival ---
        d = h - 8
        x = 4 + roll_pos * (w - d - 8)
        cy = h / 2.0
        theta = math.radians(roll_pos * 360.0)  # the moon/sun physically rolls

        # soft shadow
        sh = QColor(6, 10, 20)
        sh.setAlphaF(0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(sh)
        p.drawEllipse(QPointF(x + d / 2 + 1, cy + 1.5), d / 2, d / 2)

        # moon face — craters rotate with the roll so it reads as rolling
        moon_a = max(0.0, min(1.0, 1.0 - sky * 2.0))
        if moon_a > 0.01:
            mc = QColor(0xE4, 0xE9, 0xF2)
            mc.setAlphaF(moon_a)
            p.setBrush(mc)
            p.drawEllipse(QRectF(x, 4, d, d))
            crater = QColor(0xC2, 0xC9, 0xD8)
            crater.setAlphaF(moon_a)
            p.setBrush(crater)
            for base_deg, dist, s in ((30, 0.30, 0.16), (150, 0.26, 0.13), (255, 0.20, 0.11)):
                ang = math.radians(base_deg) + theta
                cxf = x + d / 2 + math.cos(ang) * d * dist
                cyf = cy + math.sin(ang) * d * dist
                p.drawEllipse(QPointF(cxf, cyf), d * s, d * s)

        # sun face + rays (also rolling in)
        sun_a = max(0.0, min(1.0, sky * 2.0 - 1.0))
        if sun_a > 0.01:
            sun = QColor(0xFF, 0xC8, 0x38)
            sun.setAlphaF(sun_a)
            p.setBrush(sun)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x, 4, d, d))
            glow = QRadialGradient(QPointF(x + d / 2, cy), d)
            glow.setColorAt(0.0, QColor(255, 210, 90, int(70 * sun_a)))
            glow.setColorAt(1.0, QColor(255, 210, 90, 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(x + d / 2, cy), d * 0.9, d * 0.9)
            ray = QPen(QColor(255, 200, 56, int(180 * sun_a)), 1.6)
            ray.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(ray)
            for i in range(8):
                ang = i * 45.0 + 22.5 + self._twinkle * 4.0 + math.degrees(theta)
                rr = d / 2 + 3.5
                p.drawLine(
                    QPointF(x + d / 2 + rr * _cos(ang), cy + rr * _sin(ang)),
                    QPointF(x + d / 2 + (rr + 2.5) * _cos(ang), cy + (rr + 2.5) * _sin(ang)),
                )


def _cosd(deg: float) -> float:
    return math.cos(math.radians(deg))


def _sind(deg: float) -> float:
    return math.sin(math.radians(deg))


_cos = _cosd
_sin = _sind


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        int(a.red() + (b.red() - a.red()) * t),
        int(a.green() + (b.green() - a.green()) * t),
        int(a.blue() + (b.blue() - a.blue()) * t),
    )
