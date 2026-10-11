"""Persistence of organization batches, operations and scan summaries."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from smart_file_organizer.core.models import (
    OP_MOVE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    UNDO_DONE,
    BatchRecord,
    OperationRecord,
    ScanRecord,
)
from smart_file_organizer.database.db import Database


class HistoryRepository:
    """Records and queries operation history. Never touches user files."""

    def __init__(self, database: Database) -> None:
        self._db = database

    # ----- batches -------------------------------------------------------

    def create_batch(self, kind: str, root: Path, *, dry_run: bool = False) -> str:
        batch_id = uuid.uuid4().hex
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO batches (id, kind, root_name, root_path, dry_run, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, 'running', ?)",
                (batch_id, kind, root.name or str(root), str(root), int(dry_run), time.time()),
            )
        return batch_id

    def finish_batch(self, batch_id: str, status: str, note: str | None = None) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE batches SET status = ?, note = ?, finished_at = ? WHERE id = ?",
                (status, note, time.time(), batch_id),
            )

    def get_batch(self, batch_id: str) -> BatchRecord | None:
        rows = self._db.query(_BATCH_SELECT + " WHERE b.id = ?", (batch_id,))
        return _batch_from_row(rows[0]) if rows else None

    def count_batches(self) -> int:
        rows = self._db.query("SELECT COUNT(*) AS n FROM batches")
        return int(rows[0]["n"])

    def list_batches(self, limit: int = 200) -> list[BatchRecord]:
        rows = self._db.query(_BATCH_SELECT + " ORDER BY b.created_at DESC LIMIT ?", (limit,))
        return [_batch_from_row(row) for row in rows]

    # ----- operations ----------------------------------------------------

    def record_operation(
        self,
        batch_id: str,
        op_type: str,
        *,
        source: str | None,
        destination: str | None,
        size: int | None = None,
        modified_ns: int | None = None,
        file_id: str | None = None,
        status: str = STATUS_PENDING,
        error: str | None = None,
    ) -> int:
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO operations (batch_id, op_type, source_path, destination_path, size_bytes,"
                " modified_ns, file_id, status, error, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch_id, op_type, source, destination, size, modified_ns, file_id, status, error, time.time()),
            )
            return int(cursor.lastrowid or 0)

    def update_operation(
        self, operation_id: int, status: str, *, destination: str | None = None, error: str | None = None
    ) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE operations SET status = ?, error = ?, destination_path = COALESCE(?, destination_path)"
                " WHERE id = ?",
                (status, error, destination, operation_id),
            )

    def set_undo_result(self, operation_id: int, undo_status: str, error: str | None = None) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE operations SET undo_status = ?, undo_error = ?, undone_at = ? WHERE id = ?",
                (undo_status, error, time.time(), operation_id),
            )

    def list_operations(self, batch_id: str) -> list[OperationRecord]:
        rows = self._db.query(
            "SELECT * FROM operations WHERE batch_id = ? ORDER BY id",
            (batch_id,),
        )
        return [_operation_from_row(row) for row in rows]

    def recover_interrupted(self) -> int:
        """Mark work left pending by a crash as interrupted; return how many rows changed."""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE operations SET status = ?, error = 'Application stopped before this operation finished;"
                " check the file locations manually.' WHERE status = ?",
                (STATUS_INTERRUPTED, STATUS_PENDING),
            )
            changed = cursor.rowcount
            connection.execute(
                "UPDATE batches SET status = 'interrupted', finished_at = ?,"
                " note = 'Interrupted before completion' WHERE status = 'running'",
                (time.time(),),
            )
        return changed

    def set_batch_status(self, batch_id: str, status: str, note: str | None = None) -> None:
        self.finish_batch(batch_id, status, note)

    # ----- scans and statistics ------------------------------------------

    def record_scan(
        self,
        *,
        root: Path,
        file_count: int,
        folder_count: int,
        total_bytes: int,
        category_counts: dict[str, int],
        issue_count: int,
    ) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO scans (root_name, file_count, folder_count, total_bytes, category_counts,"
                " issue_count, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    root.name or str(root),
                    file_count,
                    folder_count,
                    total_bytes,
                    json.dumps(category_counts, sort_keys=True),
                    issue_count,
                    time.time(),
                ),
            )

    def recent_scans(self, limit: int = 20) -> list[ScanRecord]:
        rows = self._db.query("SELECT * FROM scans ORDER BY created_at DESC LIMIT ?", (limit,))
        return [
            ScanRecord(
                id=row["id"],
                root_name=row["root_name"],
                file_count=row["file_count"],
                folder_count=row["folder_count"],
                total_bytes=row["total_bytes"],
                category_counts=json.loads(row["category_counts"]),
                issue_count=row["issue_count"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def operation_totals(self) -> dict[str, int]:
        """Count operations (excluding dry runs) by status, across all batches."""
        totals = {STATUS_SUCCESS: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0, STATUS_INTERRUPTED: 0, STATUS_PENDING: 0}
        rows = self._db.query(
            "SELECT o.status AS status, COUNT(*) AS n FROM operations o"
            " JOIN batches b ON b.id = o.batch_id WHERE b.dry_run = 0 AND o.op_type = ? GROUP BY o.status",
            (OP_MOVE,),
        )
        for row in rows:
            totals[row["status"]] = row["n"]
        return totals

    def bytes_moved(self) -> int:
        """Total size of files successfully moved and not yet undone."""
        rows = self._db.query(
            "SELECT COALESCE(SUM(size_bytes), 0) AS total FROM operations"
            " WHERE op_type = ? AND status = ? AND (undo_status IS NULL OR undo_status != ?)",
            (OP_MOVE, STATUS_SUCCESS, UNDO_DONE),
        )
        return int(rows[0]["total"])

    def clear(self) -> None:
        """Delete history records only. User files are never touched."""
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM operations")
            connection.execute("DELETE FROM batches")
            connection.execute("DELETE FROM scans")


_BATCH_SELECT = (
    "SELECT b.*, (SELECT COUNT(*) FROM operations o WHERE o.batch_id = b.id) AS operation_count FROM batches b"
)


def _batch_from_row(row: sqlite3.Row) -> BatchRecord:
    return BatchRecord(
        id=row["id"],
        kind=row["kind"],
        root_name=row["root_name"],
        root_path=row["root_path"],
        dry_run=bool(row["dry_run"]),
        status=row["status"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        note=row["note"],
        operation_count=row["operation_count"],
    )


def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        id=row["id"],
        batch_id=row["batch_id"],
        op_type=row["op_type"],
        source_path=row["source_path"],
        destination_path=row["destination_path"],
        size_bytes=row["size_bytes"],
        modified_ns=row["modified_ns"],
        file_id=row["file_id"],
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
        undo_status=row["undo_status"],
        undo_error=row["undo_error"],
        undone_at=row["undone_at"],
    )
