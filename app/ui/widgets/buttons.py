"""Buttons with a hand-painted pill fill for the app's primary actions."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionButton

from app.ui import theme as ui_theme


class PrimaryButton(QPushButton):
    """Antialiased brand-gradient pill that works across Qt platform styles."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("primaryButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        del event
        rect = self.rect().adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2.0
        tokens = ui_theme.tokens()
        start = QColor(tokens["PRIMARY"])
        end = QColor(tokens["PRIMARY_END"])

        if not self.isEnabled():
            start = QColor(tokens["MUTED_HEX"])
            end = QColor(tokens["MUTED_HEX"])
        elif self.isDown():
            start = start.darker(112)
            end = end.darker(112)
        elif self.underMouse():
            start = start.lighter(108)
            end = end.lighter(108)

        gradient = QLinearGradient(rect.topLeft(), rect.topRight())
        gradient.setColorAt(0.0, start)
        gradient.setColorAt(1.0, end)

        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 54 if ui_theme.current() == "dark" else 100), 1.0))
        painter.setBrush(gradient)
        painter.drawPath(shape)

        if self.hasFocus():
            focus = QColor(tokens["PRIMARY"])
            focus.setAlpha(150)
            inset = rect.adjusted(2.0, 2.0, -2.0, -2.0)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(focus, 1.2))
            painter.drawRoundedRect(inset, max(0.0, radius - 2.0), max(0.0, radius - 2.0))

        option = QStyleOptionButton()
        self.initStyleOption(option)
        label = QColor(tokens["ON_PRIMARY"] if self.isEnabled() else tokens["MUTED_HEX"])
        option.palette.setColor(QPalette.ColorRole.ButtonText, label)
        option.palette.setColor(QPalette.ColorRole.WindowText, label)
        option.palette.setColor(QPalette.ColorRole.Text, label)
        painter.save()
        painter.setClipPath(shape)
        self.style().drawControl(QStyle.ControlElement.CE_PushButtonLabel, option, painter, self)
        painter.restore()
