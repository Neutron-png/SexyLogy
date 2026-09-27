"""
LOGY entry point. Everything else in the app is importable modules;
this is the only place that owns the QApplication lifecycle.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make imports work no matter where the app is launched from (double-click,
# IDE, `py main.py` from another cwd).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.core.storage.db import Database
from app.ui.main_window import APP_VERSION, MainWindow
from app.ui import theme as ui_theme


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LOGY")
    app.setApplicationVersion(APP_VERSION)

    icon_path = Path(__file__).resolve().parent / "assets" / "logo.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # DB lives next to the app so the whole project stays portable
    # (same default "logy.db" name the storage layer and tests expect).
    db_path = Path(__file__).resolve().parent / "logy.db"
    db = Database(db_path)

    # Restore the user's saved dark/light preference before the first paint.
    ui_theme.load_fonts()
    ui_theme.set_theme(app, db.get_setting("ui_theme", "dark") or "dark")

    window = MainWindow(db)
    window.show()
    exit_code = app.exec()
    db.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
