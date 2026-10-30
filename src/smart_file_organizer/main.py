"""Application entry point: prepares data locations, opens the database, then shows the window."""

from __future__ import annotations

import logging
import sqlite3
import sys

from smart_file_organizer import __version__
from smart_file_organizer.config import (
    DATABASE_FILE_NAME,
    LOG_FILE_NAME,
    SETTINGS_FILE_NAME,
    load_settings,
)
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.watcher import WatchManager
from smart_file_organizer.database.db import open_database
from smart_file_organizer.logging_setup import configure_logging
from smart_file_organizer.utils.paths import app_data_dir

logger = logging.getLogger(__name__)


def main() -> int:
    data_dir = app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / LOG_FILE_NAME
    configure_logging(log_path)
    logger.info("Starting Smart File Organizer %s (data folder %s)", __version__, data_dir)

    settings_path = data_dir / SETTINGS_FILE_NAME
    settings = load_settings(settings_path)
    try:
        database, recovered = open_database(data_dir / DATABASE_FILE_NAME)
    except sqlite3.Error:
        logger.exception("Could not open the history database")
        print("Smart File Organizer could not open its history database. See the log file:", log_path, file=sys.stderr)
        return 1

    history = HistoryRepository(database)
    # Only the primary instance may reconcile interrupted work: a second
    # instance sharing this data folder must not mark the first instance's
    # still-running operations as interrupted.
    from smart_file_organizer.core.instance import try_acquire_instance_lock

    _instance_lock = try_acquire_instance_lock(data_dir)  # kept alive until main() returns
    if _instance_lock is not None:
        interrupted = history.recover_interrupted()
        if interrupted:
            logger.warning(
                "%d operation(s) were left unfinished by an earlier run and are marked interrupted", interrupted
            )
    else:
        logger.warning("Another instance is already running; startup recovery was skipped for this instance")

    # Imported here so that the core modules and tests never need tkinter.
    from smart_file_organizer.ui.app_window import App
    from smart_file_organizer.ui.context import AppContext

    context = AppContext(
        settings=settings,
        settings_path=settings_path,
        data_dir=data_dir,
        database=database,
        history=history,
        log_path=log_path,
        watches=WatchManager(),
        recovered_database=recovered,
    )
    app = App(context)
    if recovered is not None:
        app.after(
            300,
            lambda: app.notify(
                f"The history database was damaged and has been preserved as {recovered.name}. "
                "A new history was started."
            ),
        )
    try:
        app.mainloop()
    finally:
        database.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
