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
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from smart_file_organizer.database.migrations import migrate

logger = logging.getLogger(__name__)

_BUSY_TIMEOUT_SECONDS = 10
_SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"
_SQLITE_HEADER_SIZE = 100
_MAX_BACKUP_ATTEMPTS = 10_000


def _validate_file_header(path: Path) -> None:
    """Reject non-empty files that lack a valid SQLite 3 header before opening WAL/SHM."""
    try:
        if not path.exists():
            return
        with open(path, "rb") as handle:
            header = handle.read(_SQLITE_HEADER_SIZE)
    except OSError:
        return
    if not header:
        return
    if len(header) < _SQLITE_HEADER_SIZE or not header.startswith(_SQLITE_HEADER_MAGIC):
        raise sqlite3.DatabaseError("file is not a database")


class Database:
    """Thread-aware SQLite wrapper that applies migrations on open."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._registry_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            migrate(self.connection())
        except BaseException:
            self.close()
            raise

    def connection(self) -> sqlite3.Connection:
        """Return this thread's connection, creating it on first use."""
        connection: sqlite3.Connection | None = getattr(self._local, "connection", None)
        if connection is None:
            _validate_file_header(self.path)
            connection = sqlite3.connect(
                self.path,
                timeout=_BUSY_TIMEOUT_SECONDS,
                isolation_level=None,  # explicit transaction control below
                check_same_thread=False,
            )
            with self._registry_lock:
                self._connections.append(connection)
            self._local.connection = connection
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
            except BaseException:
                self.close()
                raise
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
        try:
            rows = self.connection().execute("PRAGMA quick_check").fetchall()
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError:
            return False
        return len(rows) == 1 and rows[0][0] == "ok"

    def close(self) -> None:
        """Close every connection opened by this instance."""
        with self._registry_lock:
            connections = list(self._connections)
            self._connections.clear()
        self._local = threading.local()
        for connection in connections:
            with suppress(sqlite3.Error, OSError):
                connection.close()


def open_database(path: Path) -> tuple[Database, Path | None]:
    """Open the history database, moving aside a corrupt file if necessary.

    Returns the database and, when recovery happened, the path of the preserved
    corrupt file. Only the application's own database file is ever renamed; user
    files are never touched. Lock and I/O errors are raised, not treated as corruption.
    """
    database: Database | None = None
    try:
        database = Database(path)
        if database.integrity_ok():
            return database, None
        database.close()
        database = None
        reason = "integrity check failed"
    except sqlite3.OperationalError:
        if database is not None:
            database.close()
        raise
    except sqlite3.DatabaseError as exc:
        if database is not None:
            database.close()
            database = None
        reason = str(exc)

    backup = _corrupt_backup_path(path)
    logger.error("History database unusable (%s); preserving it as %s", reason, backup)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{path}{suffix}")
        if candidate.exists():
            os.replace(candidate, Path(f"{backup}{suffix}"))
    return Database(path), backup


def _corrupt_backup_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
    if not _any_companion_exists(base):
        return base
    for counter in range(2, _MAX_BACKUP_ATTEMPTS):
        candidate = path.with_name(f"{path.stem}.corrupt-{stamp}-{counter}{path.suffix}")
        if not _any_companion_exists(candidate):
            return candidate
    raise FileExistsError(f"Could not find a unique backup name for {path}")


def _any_companion_exists(path: Path) -> bool:
    return any(os.path.lexists(f"{path}{suffix}") for suffix in ("", "-wal", "-shm"))
