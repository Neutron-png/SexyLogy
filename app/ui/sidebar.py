from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QButtonGroup, QFrame

ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets"

NAV_ITEMS = [
    ("new_scrape", "＋  New Scrape"),
    ("dashboard", "▦  Dashboard"),
    ("projects", "☰  Projects"),
    ("history", "◷  History"),
    ("templates", "▤  Templates"),
    ("settings", "⚙  Settings"),
    ("api_keys", "🔑  API Keys"),
    ("logs", "≡  Logs"),
]


class Sidebar(QWidget):
    navigate = Signal(str)

    def __init__(self, app_version: str = "0.1.0"):
        super().__init__()
        self.setObjectName("sidebar")
        self.setMinimumWidth(220)
        self.setMaximumWidth(240)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(2)

        logo_row = QWidget()
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(16, 14, 16, 10)
        logo_layout.setSpacing(8)

        self.logo_icon_label = QLabel()
        icon_path = ASSETS_DIR / "logo_icon_transparent.png"
        if icon_path.exists():
            pixmap = QPixmap(str(icon_path)).scaledToHeight(26, Qt.TransformationMode.SmoothTransformation)
            self.logo_icon_label.setPixmap(pixmap)
        else:
            self.logo_icon_label.setText("◆")
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
            btn.clicked.connect(lambda _checked, k=key: self.navigate.emit(k))
            self.group.addButton(btn)
            self.buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #1E293B;")
        layout.addWidget(sep)

        self.status_label = QLabel(f"LOGY v{app_version}")
        self.status_label.setStyleSheet("color: #8B95A7; font-size: 11px; padding: 6px 10px;")
        layout.addWidget(self.status_label)

        self.engine_status_label = QLabel("Engine: checking...")
        self.engine_status_label.setStyleSheet("color: #8B95A7; font-size: 11px; padding: 0 10px 8px 10px;")
        layout.addWidget(self.engine_status_label)

        self.buttons["new_scrape"].setChecked(True)

    def set_active(self, key: str):
        if key in self.buttons:
            self.buttons[key].setChecked(True)

    def set_engine_status(self, ok: bool, detail: str = ""):
        if ok:
            self.engine_status_label.setText("Engine: ● Ready")
            self.engine_status_label.setStyleSheet("color: #22C55E; font-size: 11px; padding: 0 10px 8px 10px;")
        else:
            self.engine_status_label.setText("Engine: ● Not installed")
            self.engine_status_label.setToolTip(detail)
            self.engine_status_label.setStyleSheet("color: #EF4444; font-size: 11px; padding: 0 10px 8px 10px;")

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
