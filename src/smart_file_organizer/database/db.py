"""Connection management for SQLite.

Each thread gets its own connection (background workers and the UI thread
never share one). Writes go through :meth:`Database.transaction`, which uses
``BEGIN IMMEDIATE`` so concurrent writers wait rather than fail mid-way.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from smart_file_organizer.database.migrations import migrate

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_SECONDS = 10


class Database:
    """Thread-aware SQLite wrapper that applies migrations on open."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._registry_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        migrate(self.connection())

    def connection(self) -> sqlite3.Connection:
        """Return this thread's connection, creating it on first use."""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            connection = sqlite3.connect(
                self.path,
                timeout=_BUSY_TIMEOUT_SECONDS,
                isolation_level=None,  # explicit transaction control below
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            self._local.connection = connection
            with self._registry_lock:
                self._connections.append(connection)
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside ``BEGIN IMMEDIATE``; roll back on any exception."""
        connection = self.connection()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Run a read-only statement and return all rows."""
        return self.connection().execute(sql, params).fetchall()

    def integrity_ok(self) -> bool:
        """Return True when SQLite's quick integrity check passes."""
        row = self.connection().execute("PRAGMA quick_check").fetchone()
        return row is not None and row[0] == "ok"

    def close(self) -> None:
        """Close every connection opened by this instance."""
        with self._registry_lock:
            for connection in self._connections:
                connection.close()
            self._connections.clear()
        self._local = threading.local()


def open_database(path: Path) -> tuple[Database, Path | None]:
    """Open the history database, moving aside a corrupt file if necessary.

    Returns the database and, when recovery happened, the path of the preserved
    corrupt file. Only the application's own database file is ever renamed; user
    files are never touched. Lock and I/O errors are raised, not treated as corruption.
    """
    try:
        database = Database(path)
        if database.integrity_ok():
            return database, None
        database.close()
        reason = "integrity check failed"
    except sqlite3.OperationalError:
        raise
    except sqlite3.DatabaseError as exc:
        reason = str(exc)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    logger.error("History database unusable (%s); preserving it as %s", reason, backup)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            os.replace(candidate, Path(f"{backup}{suffix}"))
    return Database(path), backup
