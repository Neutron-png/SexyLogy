"""
LOGY theme: aurora-glass design system (dark + light).

Token-driven QSS — the widget tree stays identical, only tokens flip.
Dark is the default brand; light is the same layout with daylight
materials. AuroraBackground (app/ui/widgets/aurora_bg.py) paints the
wallpaper behind translucent surfaces; ThemeToggle (switch.py) flips
the app between the two token sets at runtime.
"""
from __future__ import annotations

DARK = {
    "BG": "#070A12",
    "SURFACE": "rgba(255, 255, 255, 7%)",
    "SURFACE_2": "rgba(255, 255, 255, 11%)",
    "SURFACE_3": "rgba(255, 255, 255, 16%)",
    "BORDER": "rgba(255, 255, 255, 10%)",
    "TEXT": "#EDF1FA",
    "MUTED": "rgba(233, 239, 250, 55%)",
    "PRIMARY": "#3E7BFF",
    "PRIMARY_END": "#2EE6A8",
    "ON_PRIMARY": "#FFFFFF",
    "ACCENT_SOFT": "rgba(62, 123, 255, 16%)",
    "SUCCESS": "#2EE6A8",
    "WARNING": "#FFC24B",
    "DANGER": "#FF6B6B",
    "VIOLET": "#A78BFA",
    "INFO": "rgba(237, 241, 250, 62%)",
    "DIM": "rgba(255, 255, 255, 26%)",
    "MUTED_HEX": "#8FA3C0",
    "OK_BG": "rgba(46, 230, 168, 12%)",
    "OK_LINE": "rgba(46, 230, 168, 40%)",
    "RUN_BG": "rgba(62, 123, 255, 14%)",
    "RUN_LINE": "rgba(62, 123, 255, 45%)",
    "WARN_BG": "rgba(255, 194, 75, 12%)",
    "BAD_BG": "rgba(255, 107, 107, 12%)",
    "BAD_LINE": "rgba(255, 107, 107, 40%)",
    "BAD_TEXT": "#FF8B8B",
    "FEED_BG": "rgba(0, 0, 0, 22%)",
    "FEED_TEXT": "rgba(237, 241, 250, 82%)",
    "LINK": "#82A5FF",
    "RADIUS": 20,
    "RADIUS_S": 12,
}

LIGHT = {
    "BG": "#EDF0F7",
    "SURFACE": "rgba(255, 255, 255, 55%)",
    "SURFACE_2": "rgba(255, 255, 255, 72%)",
    "SURFACE_3": "rgba(255, 255, 255, 88%)",
    "BORDER": "rgba(18, 24, 44, 10%)",
    "TEXT": "#171B28",
    "MUTED": "rgba(24, 30, 48, 55%)",
    "PRIMARY": "#2E6BFF",
    "PRIMARY_END": "#0FBF8F",
    "ON_PRIMARY": "#FFFFFF",
    "ACCENT_SOFT": "rgba(46, 107, 255, 10%)",
    "SUCCESS": "#0E9F6E",
    "WARNING": "#B45309",
    "DANGER": "#DC2626",
    "VIOLET": "#7C5CE0",
    "INFO": "rgba(24, 30, 48, 62%)",
    "DIM": "rgba(18, 24, 44, 22%)",
    "MUTED_HEX": "#6A7288",
    "OK_BG": "rgba(14, 159, 110, 10%)",
    "OK_LINE": "rgba(14, 159, 110, 35%)",
    "RUN_BG": "rgba(46, 107, 255, 12%)",
    "RUN_LINE": "rgba(46, 107, 255, 40%)",
    "WARN_BG": "rgba(180, 83, 9, 10%)",
    "BAD_BG": "rgba(220, 38, 38, 8%)",
    "BAD_LINE": "rgba(220, 38, 38, 35%)",
    "BAD_TEXT": "#B91C1C",
    "FEED_BG": "rgba(255, 255, 255, 45%)",
    "FEED_TEXT": "rgba(23, 27, 40, 82%)",
    "LINK": "#2E6BFF",
    "RADIUS": 20,
    "RADIUS_S": 12,
}

_current = "dark"


def load_fonts() -> list[str]:
    """Register the bundled Inter font so the QSS font-family actually resolves."""
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase

    loaded: list[str] = []
    p = Path(__file__).resolve().parent.parent.parent / "assets" / "fonts" / "Inter.ttf"
    if p.exists():
        fid = QFontDatabase.addApplicationFont(str(p))
        loaded += QFontDatabase.applicationFontFamilies(fid)
    return loaded


def current() -> str:
    return _current


def tokens() -> dict:
    return DARK if _current == "dark" else LIGHT


def get_qss(mode: str | None = None) -> str:
    t = DARK if (mode or _current) == "dark" else LIGHT
    r, rs = t["RADIUS"], t["RADIUS_S"]
    return f"""
* {{
    font-family: "Inter", "SF Pro Text", "Segoe UI", system-ui, sans-serif;
    color: {t['TEXT']};
    outline: none;
    selection-background-color: {t['PRIMARY']};
    selection-color: #FFFFFF;
}}

QMainWindow, QWidget#root {{ background: transparent; }}
QStackedWidget, QStackedWidget > QWidget {{ background: transparent; }}

QWidget#sidebar {{
    background-color: {t['SURFACE']};
    border-right: 1px solid {t['BORDER']};
}}

QLabel#logo {{
    color: {t['TEXT']};
    font-size: 17px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QPushButton#navItem {{
    text-align: left;
    padding: 9px 14px;
    border-radius: 16px;
    background: transparent;
    border: none;
    color: {t['MUTED']};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#navItem:hover {{ background-color: {t['SURFACE_2']}; color: {t['TEXT']}; }}
QPushButton#navItem:checked {{
    background-color: {t['ACCENT_SOFT']};
    color: {t['TEXT']};
    font-weight: 600;
}}

QWidget#card {{
    background-color: {t['SURFACE']};
    border: 1px solid {t['BORDER']};
    border-radius: {r}px;
}}

QLabel#pageTitle {{ font-size: 23px; font-weight: 700; color: {t['TEXT']}; }}
QLabel#pageSubtitle {{ font-size: 12.5px; color: {t['MUTED']}; }}
QLabel#sectionTitle {{ font-size: 13px; font-weight: 600; color: {t['TEXT']}; }}

QPushButton, QToolButton {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover, QToolButton:hover {{ border-color: {t['PRIMARY']}; background-color: {t['SURFACE_3']}; }}
QPushButton:pressed, QToolButton:pressed {{ background-color: {t['ACCENT_SOFT']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['DIM']}; }}

QPushButton#primaryButton {{
    background-color: transparent;
    border: none;
    color: {t['ON_PRIMARY']};
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background-color: transparent; }}
QPushButton#primaryButton:disabled {{ background-color: transparent; color: {t['DIM']}; }}

QPushButton#dangerButton {{
    background-color: transparent;
    border: 1px solid {t['DANGER']};
    color: {t['DANGER']};
}}
QPushButton#dangerButton:hover {{ background-color: {t['DANGER']}; color: {t['ON_PRIMARY']}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateEdit, QTimeEdit, QDateTimeEdit {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
    padding: 7px 14px;
    min-height: 18px;
}}
QTextEdit, QPlainTextEdit {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 18px;
    padding: 8px 12px;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus,
QDateTimeEdit:focus {{ border-color: {t['PRIMARY']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QDateEdit:disabled, QTimeEdit:disabled,
QDateTimeEdit:disabled {{ color: {t['DIM']}; }}

QTableView {{
    background-color: transparent;
    alternate-background-color: {t['SURFACE_2']};
    gridline-color: transparent;
    border: none;
    selection-background-color: {t['ACCENT_SOFT']};
    selection-color: {t['TEXT']};
}}
QHeaderView {{ background-color: transparent; border: none; }}
QHeaderView::section {{
    background-color: transparent;
    color: {t['MUTED']};
    padding: 8px 10px;
    border: none;
    border-bottom: 1px solid {t['BORDER']};
    font-weight: 600;
    font-size: 11px;
}}
QTableCornerButton::section {{ background: transparent; border: none; }}

QProgressBar {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 8px;
    text-align: center;
    height: 16px;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {t['PRIMARY']}, stop:1 {t['PRIMARY_END']});
    border-radius: 8px;
}}

QTabWidget::pane {{
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {t['MUTED']};
    padding: 8px 16px;
    border-radius: 16px;
    margin: 4px 2px 2px 0;
    font-weight: 500;
}}
QTabBar::tab:selected {{ background-color: {t['ACCENT_SOFT']}; color: {t['TEXT']}; font-weight: 600; }}
QTabBar::tab:hover {{ color: {t['TEXT']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t['BORDER']}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {t['MUTED']}; }}

QLabel#statusRunning {{
    background-color: {t['RUN_BG']}; border: 1px solid {t['RUN_LINE']};
    border-radius: 12px; padding: 4px 14px;
    color: {t['PRIMARY_END']}; font-weight: 700;
}}
QLabel#statusCompleted {{
    background-color: {t['OK_BG']}; border: 1px solid {t['OK_LINE']};
    border-radius: 12px; padding: 4px 14px;
    color: {t['SUCCESS']}; font-weight: 700;
}}
QLabel#statusFailed {{
    background-color: rgba(255, 107, 107, 12%); border: 1px solid {t['DANGER']};
    border-radius: 12px; padding: 4px 14px;
    color: {t['DANGER']}; font-weight: 700;
}}
QLabel#statusPaused {{
    background-color: {t['WARN_BG']}; border: 1px solid {t['WARNING']};
    border-radius: 12px; padding: 4px 14px;
    color: {t['WARNING']}; font-weight: 700;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}

QToolBox {{ background: transparent; border: none; }}
QToolBox::tab {{
    background-color: {t['SURFACE_2']};
    color: {t['TEXT']};
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
    padding: 10px 14px;
    font-weight: 600;
}}
QToolBox::tab:selected {{ background-color: {t['ACCENT_SOFT']}; color: {t['TEXT']}; border: 1px solid {t['PRIMARY']}; }}
QToolBox::tab:hover {{ border-color: {t['PRIMARY']}; }}
QToolBox QScrollArea {{ border: none; }}
QToolBox > QWidget {{ background-color: transparent; border: none; }}

QSpinBox, QDoubleSpinBox, QDateEdit, QTimeEdit, QDateTimeEdit {{ padding-right: 44px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 22px; height: 14px; background: transparent; border: none;
    border-top-right-radius: 8px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 22px; height: 14px; background: transparent; border: none;
    border-bottom-right-radius: 8px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background-color: {t['ACCENT_SOFT']}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {t['MUTED_HEX']};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {t['MUTED_HEX']};
}}

QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QSplitter::handle:hover {{ background-color: {t['ACCENT_SOFT']}; }}

QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{
    background-color: {t['BG']};
    border: 1px solid {t['BORDER']};
    border-radius: 18px;
    color: {t['TEXT']};
    selection-background-color: {t['ACCENT_SOFT']};
    selection-color: {t['TEXT']};
    outline: none;
    padding: 4px;
}}

QListWidget, QTreeWidget, QTableWidget {{
    background-color: transparent;
    border: none;
    alternate-background-color: {t['SURFACE_2']};
    selection-background-color: {t['ACCENT_SOFT']};
    selection-color: {t['TEXT']};
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{ padding: 7px; border-radius: {rs + 2}px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {t['ACCENT_SOFT']}; color: {t['TEXT']}; }}
QListWidget::item:hover {{ background-color: {t['SURFACE_3']}; }}

QCheckBox {{ spacing: 9px; color: {t['TEXT']}; font-weight: 500; }}
QCheckBox::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {t['BORDER']};
    border-radius: 9px;
    background-color: {t['SURFACE_2']};
}}
QCheckBox::indicator:checked {{ background-color: {t['PRIMARY']}; border-color: {t['PRIMARY']}; }}
QCheckBox::indicator:hover {{ border-color: {t['PRIMARY']}; }}
QRadioButton {{ spacing: 9px; color: {t['TEXT']}; }}
QRadioButton::indicator {{
    width: 17px; height: 17px;
    border: 1px solid {t['BORDER']};
    border-radius: 9px;
    background-color: {t['SURFACE_2']};
}}
QRadioButton::indicator:checked {{ background-color: {t['PRIMARY']}; border: 4px solid {t['SURFACE_2']}; }}
QRadioButton::indicator:hover {{ border-color: {t['PRIMARY']}; }}

QGroupBox {{
    border: 1px solid {t['BORDER']};
    border-radius: {r}px;
    margin-top: 12px;
    padding-top: 10px;
    color: {t['TEXT']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['MUTED']}; }}

QToolTip {{
    background-color: {t['BG']};
    color: {t['TEXT']};
    border: 1px solid {t['BORDER']};
    padding: 5px 10px;
    border-radius: 12px;
}}

QMenu {{
    background-color: {t['BG']};
    color: {t['TEXT']};
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
    padding: 6px;
}}
QMenu::item {{ border-radius: 10px; padding: 7px 18px; }}
QMenu::item:selected {{ background-color: {t['ACCENT_SOFT']}; color: {t['TEXT']}; }}

QWidget#card > QTableView, QWidget#card > QSplitter {{ border: none; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {t['BORDER']}; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {t['MUTED']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QLabel#sideVersion {{ color: {t['MUTED']}; font-size: 11px; }}
QLabel#engineDotIdle {{ background-color: {t['DIM']}; border-radius: 4.5px; }}
QLabel#engineDotOk {{ background-color: {t['SUCCESS']}; border-radius: 4.5px; }}
QLabel#engineDotBad {{ background-color: {t['DANGER']}; border-radius: 4.5px; }}
QLabel#sideEngineOk {{ color: {t['SUCCESS']}; font-size: 11.5px; font-weight: 600; }}
QLabel#sideEngineBad {{ color: {t['DANGER']}; font-size: 11.5px; font-weight: 600; }}
QLabel#sideThemeLbl {{ color: {t['MUTED']}; font-size: 11.5px; font-weight: 500; }}

/* ---- flagship hero + accordion + run strip ---- */
QWidget#heroCard {{
    background-color: {t['SURFACE']};
    border: 1px solid {t['BORDER']};
    border-radius: 24px;
}}
QLabel#heroKicker {{ color: {t['MUTED']}; font-size: 10px; font-weight: 600; }}
QLabel#heroText {{ font-size: 19px; font-weight: 600; color: {t['TEXT']}; }}
QPushButton#heroPill {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
    padding: 5px 18px;
    font-size: 16px;
    font-weight: 600;
    color: {t['TEXT']};
}}
QPushButton#heroPill:hover {{ border-color: {t['PRIMARY']}; }}
QComboBox#heroPill {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
    padding: 5px 30px 5px 18px;
    font-size: 16px;
    font-weight: 600;
    color: {t['TEXT']};
}}
QComboBox#heroPill::drop-down {{ border: none; width: 26px; }}
QComboBox#heroPill QAbstractItemView {{ font-size: 14px; }}

QFrame#accCard {{
    background-color: {t['SURFACE']};
    border: 1px solid {t['BORDER']};
    border-radius: 20px;
}}
QLabel#accIcon {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
    color: {t['MUTED']};
    font-size: 13px;
}}
QLabel#accTitle {{ font-size: 13.5px; font-weight: 600; color: {t['TEXT']}; }}
QLabel#accSub {{ font-size: 11px; color: {t['MUTED']}; }}
QLabel#accSum {{ font-size: 12px; color: {t['MUTED']}; }}
QLabel#accChev {{ font-size: 15px; color: {t['MUTED']}; }}

QWidget#runPanel {{
    background-color: {t['SURFACE']};
    border: 1px solid {t['BORDER']};
    border-radius: 16px;
}}
QLabel#kpiMicro {{ font-size: 9.5px; font-weight: 600; color: {t['MUTED']}; }}
QLabel#kpiValue {{ font-size: 21px; font-weight: 700; color: {t['TEXT']}; }}
QLabel#feedTitle {{
    font-size: 9.5px; font-weight: 700; color: {t['MUTED']};
    letter-spacing: 1.3px;
}}
QPushButton#feedLink {{
    background: transparent; border: none;
    color: {t['LINK']}; font-size: 11.5px; font-weight: 500;
    padding: 2px 4px;
}}
QPushButton#feedLink:hover {{ color: {t['PRIMARY']}; text-decoration: underline; }}
QFrame#feedPanel {{
    background-color: {t['FEED_BG']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
}}
QLabel#feedText {{ font-size: 12px; color: {t['FEED_TEXT']}; }}
QLabel#feedDetail {{ font-size: 10.5px; color: {t['MUTED']}; }}
QLabel#feedDotOk {{ background-color: {t['SUCCESS']}; border-radius: 4px; }}
QLabel#feedDotLive {{ background-color: {t['PRIMARY']}; border-radius: 4px; }}
QLabel#feedDotWarn {{ background-color: {t['WARNING']}; border-radius: 4px; }}
QLabel#feedDotBad {{ background-color: {t['DANGER']}; border-radius: 4px; }}
QLabel#feedDotDim {{ background-color: {t['DIM']}; border-radius: 4px; }}
QFrame#tablePanel {{
    background-color: {t['FEED_BG']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
}}
QFrame#engineCard {{
    background-color: {t['SURFACE_2']};
    border: 1px solid {t['BORDER']};
    border-radius: 14px;
}}
QFrame#footHint {{
    background-color: transparent;
    border-top: 1px solid {t['BORDER']};
}}
QLabel#footHintText {{ font-size: 11px; color: {t['MUTED']}; }}
QLabel#footHintStrong {{ font-size: 11.5px; font-weight: 600; color: {t['SUCCESS']}; }}
QLabel#footDotOk {{ background-color: {t['SUCCESS']}; border-radius: 4px; }}
QFrame#sideSep {{
    border: none;
    max-height: 1px;
    background-color: {t['BORDER']};
}}
"""


def set_theme(app, mode: str) -> None:
    global _current
    _current = "light" if mode == "light" else "dark"
    app.setStyleSheet(get_qss())
