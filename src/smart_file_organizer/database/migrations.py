"""Versioned schema migrations, applied in order and tracked with ``PRAGMA user_version``."""

from __future__ import annotations

import sqlite3

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        1,
        (
            """
            CREATE TABLE batches (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                root_name TEXT NOT NULL,
                root_path TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                finished_at REAL,
                note TEXT
            )
            """,
            """
            CREATE TABLE operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                op_type TEXT NOT NULL,
                source_path TEXT,
                destination_path TEXT,
                size_bytes INTEGER,
                modified_ns INTEGER,
                file_id TEXT,
                status TEXT NOT NULL,
                error TEXT,
                created_at REAL NOT NULL,
                undo_status TEXT,
                undo_error TEXT,
                undone_at REAL
            )
            """,
            "CREATE INDEX idx_operations_batch ON operations(batch_id)",
            "CREATE INDEX idx_operations_status ON operations(status)",
            "CREATE INDEX idx_batches_created ON batches(created_at)",
            """
            CREATE TABLE scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_name TEXT NOT NULL,
                file_count INTEGER NOT NULL,
                folder_count INTEGER NOT NULL,
                total_bytes INTEGER NOT NULL,
                category_counts TEXT NOT NULL,
                issue_count INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """,
            "CREATE INDEX idx_scans_created ON scans(created_at)",
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1][0]


def migrate(connection: sqlite3.Connection) -> int:
    """Bring the database up to :data:`SCHEMA_VERSION` and return the resulting version."""
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {current} is newer than this application supports ({SCHEMA_VERSION})."
        )
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Re-read inside the write lock in case another process migrated first.
            if connection.execute("PRAGMA user_version").fetchone()[0] < version:
                for statement in statements:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {int(version)}")
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        current = version
    return current
