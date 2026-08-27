from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QIcon, QGuiApplication
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
        # "خلي الديزاين يشتغل على اي شاشة" - a fixed minimumSize(1100, 700)
        # + resize(1360, 860) opened wider/taller than smaller laptop
        # screens (e.g. a 1280x720 or 1366x768 display, common on
        # budget/older Windows laptops and small MacBooks) can actually
        # show, so the window either opened partly off-screen or Qt
        # silently clamped it in a way that made parts of the UI
        # unreachable. Size relative to the ACTUAL screen this window is
        # opening on instead of hardcoded pixel constants: minimum size
        # is small enough to fit on any display Windows/macOS still
        # supports today, and the initial size is a comfortable fraction
        # of whatever's actually available (never larger than the
        # screen), so the same code looks right on a small laptop panel
        # and a large external monitor alike. Every screen inside the
        # app already uses QScrollArea / QSplitter / stretch layouts (no
        # other fixed pixel widths on the root layout), so shrinking the
        # window doesn't clip content - it scrolls instead.
        self._MIN_W, self._MIN_H = 900, 600
        self._fit_to_screen(QGuiApplication.primaryScreen(), center=True)
        # The check above only covers the screen the window OPENS on. A
        # laptop dragged to an external monitor mid-session (very common:
        # different resolution AND different DPI than the built-in panel)
        # needs the same fit re-applied to the NEW screen, or the window
        # keeps whatever size/position made sense on the old one - this
        # is the rest of "خلي الديزاين يشتغل على اي شاشة"، مش بس أول ما
        # يفتح. windowHandle() only exists once the window has an actual
        # platform window, so this is wired up in showEvent() below
        # rather than here.
        self._screen_watch_connected = False

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

    def _fit_to_screen(self, screen, center: bool):
        """Sizes the window to whatever screen it's actually on - a
        comfortable 85% of that screen's available area (never larger
        than the screen, so it can never open partly off-screen or get
        silently clamped), with a minimum small enough to fit on any
        display Windows/macOS still supports. Reused both at startup and
        whenever the window moves to a different screen - see
        showEvent()/_on_screen_changed() below."""
        available = screen.availableGeometry() if screen else None
        if available is None:  # pragma: no cover - no screen available (headless test env)
            self.setMinimumSize(self._MIN_W, self._MIN_H)
            self.resize(1360, 860)
            return
        self.setMinimumSize(min(self._MIN_W, available.width()), min(self._MIN_H, available.height()))
        target_w = min(1360, int(available.width() * 0.85))
        target_h = min(860, int(available.height() * 0.85))
        self.resize(max(target_w, self.minimumWidth()), max(target_h, self.minimumHeight()))
        if center:
            # Center on the screen rather than defaulting to whatever
            # corner Qt happens to place a freshly-resized window at.
            frame = self.frameGeometry()
            frame.moveCenter(available.center())
            self.move(frame.topLeft())

    def showEvent(self, event):
        super().showEvent(event)
        # windowHandle() only exists once the widget has a real platform
        # window - not yet available in __init__, hence wiring this up
        # here instead. Guarded so a second show() (e.g. after being
        # minimized) doesn't stack duplicate connections.
        if not self._screen_watch_connected:
            handle = self.windowHandle()
            if handle is not None:
                handle.screenChanged.connect(self._on_screen_changed)
                self._screen_watch_connected = True

    def _on_screen_changed(self, screen):
        # Re-fit WITHOUT re-centering - the user just dragged the window
        # there on purpose, so only the size (not the position) should
        # adapt to the new screen's dimensions/DPI.
        self._fit_to_screen(screen, center=False)

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
