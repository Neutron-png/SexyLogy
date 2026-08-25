from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QIcon
from PySide6.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QStackedWidget

from app.core.job_manager import JobManager
from app.core.storage.db import Database
from app.core.engine.builtin_templates import seed_builtin_templates
from app.core.engine import scrapling_adapter as engine

from app.ui.sidebar import Sidebar
from app.ui.screens.new_scrape import NewScrapeScreen
from app.ui.screens.dashboard import DashboardScreen
from app.ui.screens.projects import ProjectsScreen
from app.ui.screens.history import HistoryScreen
from app.ui.screens.templates import TemplatesScreen
from app.ui.screens.settings import SettingsScreen
from app.ui.screens.api_keys import ApiKeysScreen
from app.ui.screens.logs import LogsScreen

APP_VERSION = "0.1.0"


class MainWindow(QMainWindow):
    def __init__(self, db: Database):
        super().__init__()
        self.setWindowTitle("LOGY")
        self.setMinimumSize(1100, 700)
        self.resize(1360, 860)

        assets_dir = Path(__file__).resolve().parent.parent.parent / "assets"
        icon_path = assets_dir / "logo.ico"
        if not icon_path.exists():
            icon_path = assets_dir / "logo_icon.png"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self.db = db
        seed_builtin_templates(self.db)
        self.job_manager = JobManager(self.db)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.sidebar = Sidebar(APP_VERSION)
        root_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        root_layout.addWidget(self.stack, 1)

        self.new_scrape_screen = NewScrapeScreen(self.db, self.job_manager)
        self.dashboard_screen = DashboardScreen(self.db)
        self.projects_screen = ProjectsScreen(self.db)
        self.history_screen = HistoryScreen(self.db)
        self.templates_screen = TemplatesScreen(self.db)
        self.settings_screen = SettingsScreen(self.db)
        self.api_keys_screen = ApiKeysScreen(self.db)
        self.logs_screen = LogsScreen(self.db)

        self.screens = {
            "new_scrape": self.new_scrape_screen,
            "dashboard": self.dashboard_screen,
            "projects": self.projects_screen,
            "history": self.history_screen,
            "templates": self.templates_screen,
            "settings": self.settings_screen,
            "api_keys": self.api_keys_screen,
            "logs": self.logs_screen,
        }
        for screen in self.screens.values():
            self.stack.addWidget(screen)

        self.sidebar.navigate.connect(self._navigate)
        self.projects_screen.open_project.connect(self._open_project_in_new_scrape)
        self.templates_screen.use_template.connect(self._use_template_in_new_scrape)

        self.sidebar.set_engine_status(engine.SCRAPLING_AVAILABLE, engine.SCRAPLING_IMPORT_ERROR or "")
        self._navigate("new_scrape")
        self._setup_shortcuts()

    def _navigate(self, key: str):
        screen = self.screens.get(key)
        if screen:
            self.stack.setCurrentWidget(screen)
            self.sidebar.set_active(key)

    def _open_project_in_new_scrape(self, project_id: int):
        import json
        from app.core.models import ExtractionField
        project = self.db.get_project(project_id)
        if not project:
            return
        config = json.loads(project["config_json"])
        fields = [ExtractionField.from_dict(f) for f in config.get("fields", [])]
        self.new_scrape_screen.field_builder.load_fields(fields)
        target = config.get("target", {})
        self.new_scrape_screen.urls_input.setPlainText("\n".join(target.get("start_urls", [])))
        self._navigate("new_scrape")

    def _use_template_in_new_scrape(self, config: dict):
        from app.core.models import ExtractionField
        fields = [ExtractionField.from_dict(f) for f in config.get("fields", [])]
        self.new_scrape_screen.field_builder.load_fields(fields)
        self._navigate("new_scrape")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self._navigate("new_scrape"))
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.new_scrape_screen._save_project)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.new_scrape_screen._start_scraping)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.new_scrape_screen._export_results)

    def closeEvent(self, event):
        if self.job_manager.is_running:
            self.job_manager.stop()
        self.db.close()
        super().closeEvent(event)
