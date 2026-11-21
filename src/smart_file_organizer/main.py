"""Application entry point: acquire the data-folder lock, recover history, then show the window."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from smart_file_organizer import __version__
from smart_file_organizer.config import DATABASE_FILE_NAME, LOG_FILE_NAME, SETTINGS_FILE_NAME, load_settings
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.instance import try_acquire_instance_lock
from smart_file_organizer.database.db import open_database
from smart_file_organizer.logging_setup import configure_logging
from smart_file_organizer.utils.paths import DATA_DIR_ENV_VAR, app_data_dir

logger = logging.getLogger(__name__)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="smart-file-organizer")
    parser.add_argument("--smoke-test", action="store_true", help="Run a desktop smoke test in an empty SFO_DATA_DIR")
    smoke = parser.parse_args(argv).smoke_test
    data_dir = app_data_dir().absolute()
    try:
        if smoke and (not os.environ.get(DATA_DIR_ENV_VAR) or (data_dir.exists() and any(data_dir.iterdir()))):
            message = f"--smoke-test requires an empty directory set via {DATA_DIR_ENV_VAR}."
            return _startup_error(message, smoke=smoke)
        data_dir.mkdir(parents=True, exist_ok=True)
        lock = try_acquire_instance_lock(data_dir)
        if lock is None:
            return _startup_error(
                f"Smart File Organizer could not lock its data folder:\n{data_dir}\n\n"
                "Close the other instance or check your permissions, then try again.",
                smoke=smoke,
            )
        # Keep the lock through startup recovery, every write, and database close.
        with lock:
            return _run(data_dir, smoke=smoke)
    except (OSError, RuntimeError, sqlite3.Error) as exc:
        logger.exception("Smart File Organizer could not start")
        return _startup_error(f"Smart File Organizer could not start: {exc}", smoke=smoke)


def _run(data_dir: Path, *, smoke: bool) -> int:
    log_path = data_dir / LOG_FILE_NAME
    configure_logging(log_path)
    logger.info("Starting Smart File Organizer %s (data folder %s)", __version__, data_dir)
    settings_path = data_dir / SETTINGS_FILE_NAME
    settings = load_settings(settings_path)
    database, recovered = open_database(data_dir / DATABASE_FILE_NAME)
    try:
        history = HistoryRepository(database)
        interrupted = history.recover_interrupted()
        if interrupted:
            logger.warning("%d operation(s) were left unfinished and need recovery guidance", interrupted)

        # Keep tkinter out of headless logic and failure paths before startup.
        try:
            from tkinter import TclError

            from smart_file_organizer.ui.app_window import App
            from smart_file_organizer.ui.context import AppContext
        except ImportError as exc:
            raise RuntimeError("The desktop requires Tcl/Tk and CustomTkinter. See the setup guide.") from exc

        context = AppContext(
            settings=settings,
            settings_path=settings_path,
            data_dir=data_dir,
            database=database,
            history=history,
            log_path=log_path,
            recovered_database=recovered,
        )
        try:
            app = App(context)
        except TclError as exc:
            raise RuntimeError(f"Could not open the desktop window: {exc}") from exc
        if smoke:
            from smart_file_organizer.ui.startup_smoke import schedule_smoke

            return schedule_smoke(app)
        if recovered is not None:
            app.after(
                300,
                lambda: app.notify(
                    f"The history database was damaged and has been preserved as {recovered.name}. "
                    "A new history was started."
                ),
            )
        app.mainloop()
        return 0
    finally:
        database.close()


def _startup_error(message: str, *, smoke: bool) -> int:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    elif sys.platform == "win32" and not smoke:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Smart File Organizer", 0x10)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
