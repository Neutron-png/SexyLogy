"""
LOGY entry point.

Run with:  python main.py
Package with PyInstaller (see README.md, section "Packaging").
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))


def _data_dir() -> Path:
    import os
    base = os.environ.get("APPDATA") or os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    d = Path(base) / "LOGY"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from app.ui.theme import QSS
    from app.ui.main_window import MainWindow
    from app.core.storage.db import Database

    # "اظبط الريشيو بتاع الشاشات" - on a machine with more than one
    # monitor at DIFFERENT DPI (a laptop's built-in 100% panel next to a
    # 150%/200% external 4K display, or vice versa - very common on
    # Windows), Qt has to pick ONE way to convert its logical layout
    # pixels to each screen's real pixels. PassThrough tells it to use
    # each screen's OWN scale factor exactly (instead of rounding every
    # screen to the same whole-number factor, which is what previously
    # made the window/UI look mis-sized - too small or too big, "wrong
    # ratio" - the moment it was dragged onto a monitor with a different
    # DPI than the one Qt started on). Must be set before QApplication()
    # exists - Qt reads it once at construction.
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("LOGY")
    app.setStyleSheet(QSS)

    icon_path = APP_DIR / "assets" / "logo.ico"
    if not icon_path.exists():
        icon_path = APP_DIR / "assets" / "logo_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))  # taskbar + alt-tab icon

    db_path = _data_dir() / "logy.db"
    db = Database(db_path)

    window = MainWindow(db)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
