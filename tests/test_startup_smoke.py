"""GUI startup smoke test.

The rest of the suite covers the core logic without tkinter, so a broken
import, an unsupported widget option or a page that raises while building
would only show up on a real desktop. This test builds the actual window with
every page, walks the navigation and closes it again.

It runs wherever a desktop session exists - Windows and macOS runners, and
Linux with ``DISPLAY`` - and skips on a headless runner, where there is no X
server to draw on. Set ``SFO_REQUIRE_GUI=1`` to turn a skip into a failure,
which is what the Windows CI job does so a missing desktop session cannot make
the check pass silently.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

from smart_file_organizer.config import DATABASE_FILE_NAME, SETTINGS_FILE_NAME, load_settings
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.database.db import open_database
from smart_file_organizer.ui.context import AppContext


def _desktop_available() -> bool:
    if importlib.util.find_spec("tkinter") is None:
        return False
    if os.name == "nt" or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


REQUIRE_GUI = os.environ.get("SFO_REQUIRE_GUI") == "1"


def require_desktop() -> None:
    """Skip on a headless runner, unless the environment insists on a real window."""
    if _desktop_available():
        return
    message = "No display or no tkinter here"
    if REQUIRE_GUI:
        pytest.fail(f"{message}, but SFO_REQUIRE_GUI=1 was set")
    pytest.skip(message)


def test_window_opens_every_page_and_closes(tmp_path: Path) -> None:
    require_desktop()
    pytest.importorskip("customtkinter")
    from smart_file_organizer.ui.app_window import NAV_ITEMS, App

    data_dir = tmp_path / "appdata"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings_path = data_dir / SETTINGS_FILE_NAME
    database, recovered = open_database(data_dir / DATABASE_FILE_NAME)
    assert recovered is None
    app = None
    try:
        context = AppContext(
            settings=load_settings(settings_path),
            settings_path=settings_path,
            data_dir=data_dir,
            database=database,
            history=HistoryRepository(database),
            log_path=data_dir / "app.log",
        )
        app = App(context)
        app.update_idletasks()
        assert app.winfo_exists()

        # Every page must build, show and hide without raising.
        for key, _label in NAV_ITEMS:
            app.show_page(key)
            app.update_idletasks()
            assert app.page(key).winfo_exists()

        app.refresh_views()
        app.update_idletasks()
    finally:
        if app is not None:
            app.on_close()  # the real shutdown path: stops monitoring, destroys the window
        database.close()


def test_startup_survives_a_corrupt_settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hand-edited settings file must not stop the application from starting."""
    require_desktop()
    pytest.importorskip("customtkinter")
    from smart_file_organizer.ui.app_window import App
    from smart_file_organizer.utils.paths import DATA_DIR_ENV_VAR

    data_dir = tmp_path / "appdata"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(data_dir))
    settings_path = data_dir / SETTINGS_FILE_NAME
    settings_path.write_text(
        '{"excluded_folders": [".git", 5], "category_folders": {"Documents": 9}}', encoding="utf-8"
    )

    database, _ = open_database(data_dir / DATABASE_FILE_NAME)
    app = None
    try:
        context = AppContext(
            settings=load_settings(settings_path),
            settings_path=settings_path,
            data_dir=data_dir,
            database=database,
            history=HistoryRepository(database),
            log_path=data_dir / "app.log",
        )
        app = App(context)
        app.show_page("settings")
        app.update_idletasks()
    finally:
        if app is not None:
            app.on_close()
        database.close()
