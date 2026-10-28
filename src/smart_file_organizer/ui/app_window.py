"""Main window: navigation, status line, shared scan action and page lifecycle."""

from __future__ import annotations

import logging
from typing import Any

import customtkinter as ctk

from smart_file_organizer.core.models import ScanOptions, ScanResult
from smart_file_organizer.core.scanner import scan_folder
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.sidebar import Sidebar
from smart_file_organizer.ui.components.tasks import TaskContext, TaskRunner
from smart_file_organizer.ui.context import AppContext

logger = logging.getLogger(__name__)

SCAN_KEY = "scan"
NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("dashboard", "Dashboard"),
    ("organize", "Organize Files"),
    ("duplicates", "Duplicate Finder"),
    ("large", "Large Files"),
    ("automation", "Automation"),
    ("history", "History"),
    ("settings", "Settings"),
    ("about", "About"),
)


class App(ctk.CTk):
    """Top-level window. Pages are created once and shown or hidden."""

    def __init__(self, context: AppContext) -> None:
        super().__init__()
        self.ctx = context
        self.title("Smart File Organizer")
        self.geometry("1240x820")
        self.minsize(1040, 700)
        theme.configure_appearance(context.settings.theme)
        self.configure(fg_color=theme.BG)
        self.runner = TaskRunner(self)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.sidebar = Sidebar(self, NAV_ITEMS, on_select=self.show_page)
        self.sidebar.grid(row=0, column=0, sticky="ns")

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=(20, 8))
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(self, text="", text_color=theme.MUTED, anchor="w")
        self.status.grid(row=1, column=1, sticky="ew", padx=24, pady=(0, 10))

        from smart_file_organizer.ui.pages.about import AboutPage
        from smart_file_organizer.ui.pages.automation import AutomationPage
        from smart_file_organizer.ui.pages.dashboard import DashboardPage
        from smart_file_organizer.ui.pages.duplicates import DuplicatesPage
        from smart_file_organizer.ui.pages.history import HistoryPage
        from smart_file_organizer.ui.pages.large_files import LargeFilesPage
        from smart_file_organizer.ui.pages.organize import OrganizePage
        from smart_file_organizer.ui.pages.settings import SettingsPage

        factories = {
            "dashboard": DashboardPage,
            "organize": OrganizePage,
            "duplicates": DuplicatesPage,
            "large": LargeFilesPage,
            "automation": AutomationPage,
            "history": HistoryPage,
            "settings": SettingsPage,
            "about": AboutPage,
        }
        self._pages: dict[str, Any] = {}
        for key, factory in factories.items():
            page = factory(self.content, self)
            page.grid(row=0, column=0, sticky="nsew")
            page.grid_remove()
            self._pages[key] = page
        self._current: str | None = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show_page("dashboard")

    # ----- navigation ----------------------------------------------------

    def page(self, key: str) -> Any:
        return self._pages[key]

    def show_page(self, key: str) -> None:
        if self._current == key:
            return
        if self._current is not None:
            self._pages[self._current].on_hide()
            self._pages[self._current].grid_remove()
        page = self._pages[key]
        page.grid()
        page.on_show()
        self._current = key
        self.sidebar.set_active(key)

    def notify(self, message: str) -> None:
        self.status.configure(text=message)

    def refresh_views(self) -> None:
        """Ask every page to redraw from the shared state (after a scan or change)."""
        for page in self._pages.values():
            page.refresh()

    # ----- shared scan --------------------------------------------------

    def start_scan(self, folder: str, recursive: bool, *, on_done: Any = None) -> bool:
        """Scan ``folder`` in the background and publish the result as the current scan."""
        from smart_file_organizer.ui.pages.base import validate_folder

        try:
            path = validate_folder(folder)
        except ValueError as exc:
            dialogs.error(self, "Choose a folder", str(exc))
            return False
        settings = self.ctx.settings
        options = ScanOptions(
            recursive=recursive,
            excluded_folders=tuple(settings.excluded_folders),
            excluded_patterns=tuple(settings.excluded_patterns),
        )
        classifier = self.ctx.classifier

        def work(task: TaskContext) -> ScanResult:
            def on_progress(progress: Any) -> None:
                task.report(f"Scanning… {progress.files_seen:,} files, {progress.folders_seen:,} folders")

            result = scan_folder(path, options, classifier, progress=on_progress, cancel_event=task.cancel_event)
            if not result.cancelled:
                self.ctx.history.record_scan(
                    root=result.root,
                    file_count=result.file_count,
                    folder_count=result.folders_scanned,
                    total_bytes=result.total_bytes,
                    category_counts=result.category_counts,
                    issue_count=len(result.issues),
                )
            return result

        def success(result: ScanResult) -> None:
            self.ctx.last_scan = result
            self.ctx.last_plan = None
            self._set_running(False)
            if result.cancelled:
                self.notify("Scan cancelled. Results shown are incomplete.")
            else:
                notes = f" · {len(result.issues)} item(s) could not be read" if result.issues else ""
                self.notify(f"Scanned {result.file_count:,} files in {result.root.name}{notes}")
            self.refresh_views()
            if on_done is not None:
                on_done(result)

        def failure(message: str) -> None:
            self._set_running(False)
            dialogs.error(self, "Scan failed", message)
            self.notify("Scan failed.")

        started = self.runner.submit(
            SCAN_KEY,
            work,
            on_success=success,
            on_error=failure,
            on_progress=self.notify,
        )
        if started:
            self._set_running(True)
            self.notify(f"Scanning {path.name}…")
        return started

    def cancel_scan(self) -> None:
        self.runner.cancel(SCAN_KEY)
        self.notify("Cancelling scan…")

    def _set_running(self, running: bool) -> None:
        for page in self._pages.values():
            if hasattr(page, "set_scan_running"):
                page.set_scan_running(running)

    # ----- lifecycle ---------------------------------------------------

    def on_close(self) -> None:
        if self.runner.any_busy():
            if not dialogs.confirm(
                self,
                "Operation in progress",
                "A scan or file operation is still running. Closing will cancel it safely: "
                "the current file operation finishes, no further files are touched, and anything "
                "already moved stays recorded. Close and cancel?",
            ):
                return
            self.notify("Cancelling running operations before closing...")
            if not self.runner.shutdown(timeout=10.0):
                # A worker is still finishing a filesystem operation. Closing
                # (and releasing the database) underneath it is not safe, so
                # the window stays open and the user can retry in a moment.
                dialogs.info(
                    self,
                    "Still finishing",
                    "An operation is still finishing its current file and could not be stopped yet. "
                    "Wait a moment, then close again.",
                )
                self.notify("Close cancelled: an operation is still finishing.")
                return
        self.ctx.watches.stop_all(timeout=3.0)  # bounded: a slow folder must not block closing
        logger.info("Application closing")
        self.destroy()
