from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QButtonGroup, QFrame,
)

from app.ui import theme as ui_theme
from app.ui.widgets import icons
from app.ui.widgets.sparkline import Sparkline

NAV_ICON = {
    "new_scrape": "new",
    "dashboard": "dashboard",
    "projects": "projects",
    "history": "history",
    "templates": "templates",
    "settings": "settings",
    "api_keys": "key",
    "logs": "logs",
}

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

NAV_ITEMS = [
    ("new_scrape", "New Campaign"),
    ("dashboard", "Dashboard"),
    ("projects", "Projects"),
    ("history", "History"),
    ("templates", "Templates"),
    ("settings", "Settings"),
    ("api_keys", "AI Setup"),
    ("logs", "Activity"),
]


class Sidebar(QWidget):
    navigate = Signal(str)
    repair_requested = Signal()

    def __init__(self, app_version: str = "0.1.0"):
        super().__init__()
        self.setObjectName("sidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 10)
        layout.setSpacing(2)

        logo_row = QWidget()
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(10, 14, 10, 12)
        logo_layout.setSpacing(8)

        self.logo_icon_label = QLabel()
        icon_path = ASSETS_DIR / "logo_icon_transparent.png"
        if icon_path.exists():
            dpr = icons.device_pixel_ratio()
            pixmap = QPixmap(str(icon_path)).scaledToHeight(
                round(26 * dpr), Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(dpr)
            self.logo_icon_label.setPixmap(pixmap)
        else:
            self.logo_icon_label.setText("")
        logo_layout.addWidget(self.logo_icon_label)

        self.logo_text_label = QLabel("LOGY")
        self.logo_text_label.setObjectName("logo")
        self.logo_text_label.setStyleSheet("padding: 0;")
        logo_layout.addWidget(self.logo_text_label)
        logo_layout.addStretch(1)

        layout.addWidget(logo_row)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}

        for key, label in NAV_ITEMS:
            btn = QPushButton(label)
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIconSize(QSize(17, 17))
            btn.clicked.connect(lambda _checked, k=key: self.navigate.emit(k))
            self.group.addButton(btn)
            self.buttons[key] = btn
            layout.addWidget(btn)
        self.refresh_icons()

        layout.addStretch(1)

        # ---- engine status card (prototype style) ----
        card = QFrame()
        card.setObjectName("engineCard")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(4)

        eng_row = QWidget()
        er = QHBoxLayout(eng_row)
        er.setContentsMargins(0, 0, 0, 0)
        er.setSpacing(7)
        self.engine_dot = QLabel()
        self.engine_dot.setFixedSize(9, 9)
        self.engine_dot.setObjectName("engineDotIdle")
        er.addWidget(self.engine_dot)
        self.engine_status_label = QLabel("Engine: checking...")
        self.engine_status_label.setObjectName("sideVersion")
        er.addWidget(self.engine_status_label)
        er.addStretch(1)
        cv.addWidget(eng_row)

        # One-click repair when the the fetch engine engine (or its browsers) is
        # missing on this machine - runs the documented installer for the
        # optional-dependencies extras and refreshes the status.
        self.engine_repair_btn = QPushButton("Repair engine")
        self.engine_repair_btn.setObjectName("feedLink")
        self.engine_repair_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.engine_repair_btn.setVisible(False)
        self.engine_repair_btn.clicked.connect(self.repair_requested.emit)
        cv.addWidget(self.engine_repair_btn)

        prot = QLabel("Protection · rotating identities")
        prot.setObjectName("sideVersion")
        cv.addWidget(prot)

        self.spark = Sparkline()
        cv.addWidget(self.spark)

        self.status_label = QLabel(f"LOGY v{app_version}")
        self.status_label.setObjectName("sideVersion")
        cv.addWidget(self.status_label)

        layout.addWidget(card)

    def set_active(self, key: str):
        if key in self.buttons:
            self.buttons[key].setChecked(True)
        self.refresh_icons()

    def refresh_icons(self):
        dark = ui_theme.current() == "dark"
        active_hex = "#EDF1FA" if dark else "#171B28"
        idle_hex = "#8FA3C0" if dark else "#6A7288"
        for key, btn in self.buttons.items():
            checked = btn.isChecked()
            btn.setIcon(icons.icon(NAV_ICON.get(key, "new"), active_hex if checked else idle_hex, 17))

    def set_engine_status(self, ok: bool, detail: str = "", *, installing: bool = False):
        if installing:
            self.engine_status_label.setText("Engine: installing...")
            self.engine_status_label.setObjectName("sideVersion")
            self.engine_dot.setObjectName("engineDotIdle")
            self.engine_repair_btn.setText("Installing...")
            self.engine_repair_btn.setEnabled(False)
            self.engine_repair_btn.setVisible(True)
        elif ok:
            self.engine_status_label.setText("Engine online")
            self.engine_status_label.setObjectName("sideEngineOk")
            self.engine_dot.setObjectName("engineDotOk")
            self.engine_repair_btn.setVisible(False)
            self.engine_repair_btn.setEnabled(True)
        else:
            self.engine_status_label.setText("Engine offline")
            self.engine_status_label.setToolTip(detail)
            self.engine_status_label.setObjectName("sideEngineBad")
            self.engine_dot.setObjectName("engineDotBad")
            self.engine_repair_btn.setText("Repair engine")
            self.engine_repair_btn.setEnabled(True)
            self.engine_repair_btn.setVisible(True)
        for w in (self.engine_status_label, self.engine_dot, self.engine_repair_btn):
            w.style().unpolish(w)
            w.style().polish(w)

    def toggle_collapsed(self):
        collapsed = self.maximumWidth() > 90
        if collapsed:
            self.setMaximumWidth(64)
            self.setMinimumWidth(64)
            self.logo_text_label.setVisible(False)
            for key, btn in self.buttons.items():
                btn.setText(dict(NAV_ITEMS)[key].split("  ")[0])
        else:
            self.setMaximumWidth(240)
            self.setMinimumWidth(220)
            self.logo_text_label.setVisible(True)
            for key, btn in self.buttons.items():
                btn.setText(dict(NAV_ITEMS)[key])
