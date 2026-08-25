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
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from app.ui.theme import QSS
    from app.ui.main_window import MainWindow
    from app.core.storage.db import Database

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
