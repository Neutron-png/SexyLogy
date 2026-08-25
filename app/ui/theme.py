"""
LOGY dark theme: design tokens + generated QSS.

Palette follows the brand spec exactly (deep navy background, electric
blue / cyan / violet accents, card surfaces #0B1220 / #101827, subtle
1px borders, 10-14px radii, no heavy glow).
"""

BG = "#070B14"
SURFACE = "#0B1220"
SURFACE_2 = "#101827"
BORDER = "#1E293B"
TEXT = "#E5E9F0"
TEXT_MUTED = "#8B95A7"
PRIMARY = "#3B82F6"      # electric blue
CYAN = "#22D3EE"
VIOLET = "#8B5CF6"
SUCCESS = "#22C55E"
WARNING = "#F59E0B"
DANGER = "#EF4444"

RADIUS = 12

QSS = f"""
* {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    color: {TEXT};
    outline: none;
}}

QMainWindow, QWidget#root {{
    background-color: {BG};
}}

QWidget#sidebar {{
    background-color: {SURFACE};
    border-right: 1px solid {BORDER};
}}

QLabel#logo {{
    color: {TEXT};
    font-size: 18px;
    font-weight: 700;
    padding: 18px 16px;
}}

QPushButton#navItem {{
    text-align: left;
    padding: 10px 14px;
    border-radius: {RADIUS - 2}px;
    background: transparent;
    border: none;
    color: {TEXT_MUTED};
    font-size: 13px;
}}
QPushButton#navItem:hover {{
    background-color: {SURFACE_2};
    color: {TEXT};
}}
QPushButton#navItem:checked {{
    background-color: rgba(59, 130, 246, 0.15);
    color: {PRIMARY};
    font-weight: 600;
}}

QWidget#card {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}

QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 700;
    color: {TEXT};
}}
QLabel#pageSubtitle {{
    font-size: 13px;
    color: {TEXT_MUTED};
}}
QLabel#sectionTitle {{
    font-size: 14px;
    font-weight: 600;
    color: {TEXT};
}}

QPushButton {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
}}
QPushButton:hover {{ border-color: {PRIMARY}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; }}

QPushButton#primaryButton {{
    background-color: {PRIMARY};
    border: none;
    color: white;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{ background-color: #2563EB; }}
QPushButton#primaryButton:disabled {{ background-color: {SURFACE_2}; color: {TEXT_MUTED}; }}

QPushButton#dangerButton {{
    background-color: transparent;
    border: 1px solid {DANGER};
    color: {DANGER};
}}
QPushButton#dangerButton:hover {{ background-color: rgba(239, 68, 68, 0.1); }}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {PRIMARY};
}}

QTableView {{
    background-color: {SURFACE};
    alternate-background-color: {SURFACE_2};
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
}}
QHeaderView::section {{
    background-color: {SURFACE_2};
    color: {TEXT_MUTED};
    padding: 6px;
    border: none;
    border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}

QProgressBar {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 6px;
    text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 6px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_MUTED};
    padding: 8px 16px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {PRIMARY};
}}

QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}

QLabel#statusRunning {{ color: {PRIMARY}; font-weight: 600; }}
QLabel#statusCompleted {{ color: {SUCCESS}; font-weight: 600; }}
QLabel#statusFailed {{ color: {DANGER}; font-weight: 600; }}
QLabel#statusPaused {{ color: {WARNING}; font-weight: 600; }}

/* ---- previously-unstyled widgets: without these rules Qt falls back
   to the native OS widget chrome (light/white on Windows), which is
   what shows up as a "border leak" through the dark theme. ---- */

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}

QToolBox {{ background: transparent; border: none; }}
QToolBox::tab {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 12px;
    font-weight: 600;
}}
QToolBox::tab:selected {{ color: {PRIMARY}; border-color: {PRIMARY}; }}
QToolBox::tab:hover {{ border-color: {PRIMARY}; }}
QToolBox QScrollArea {{ border: none; }}
QToolBox > QWidget {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-top: none; border-radius: 0 0 8px 8px; }}

QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:vertical {{ height: 10px; }}
QSplitter::handle:hover {{ background-color: rgba(59, 130, 246, 0.12); }}

QComboBox {{ padding-right: 24px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background-color: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    color: {TEXT};
    selection-background-color: rgba(59, 130, 246, 0.25);
    selection-color: {TEXT};
    outline: none;
    padding: 4px;
}}

QListWidget, QTreeWidget, QTableWidget {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    alternate-background-color: {SURFACE_2};
    outline: none;
}}
QListWidget::item, QTreeWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: rgba(59, 130, 246, 0.18); color: {TEXT}; }}
QListWidget::item:hover {{ background-color: {SURFACE_2}; }}

QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 4px;
    background-color: {SURFACE_2};
}}
QCheckBox::indicator:checked {{ background-color: {PRIMARY}; border-color: {PRIMARY}; }}
QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: {RADIUS}px;
    margin-top: 12px;
    padding-top: 10px;
    color: {TEXT};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT_MUTED}; }}

QToolTip {{
    background-color: {SURFACE_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 8px;
    border-radius: 6px;
}}

/* A card that contains a widget flush against its own edge (a table,
   a splitter) was double-bordering - the inner widget's own 1px border
   plus the card's border looked like a stray line ("border leak"). Kill
   the inner border when it's the direct child of a card. */
QWidget#card > QTableView, QWidget#card > QSplitter {{ border: none; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
"""
