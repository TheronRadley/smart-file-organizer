import os
import sqlite3
from pathlib import Path

import pytest

from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import OP_MOVE, STATUS_PENDING, STATUS_SUCCESS
from smart_file_organizer.database.db import Database, open_database
from smart_file_organizer.database.migrations import SCHEMA_VERSION


def test_schema_is_initialised_at_current_version(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.sqlite3")
    try:
        assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
        tables = {row["name"] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"batches", "operations", "scans"} <= tables
    finally:
        db.close()


def test_reopening_does_not_repeat_migrations(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    Database(path).close()
    db = Database(path)
    try:
        assert db.query("PRAGMA user_version")[0][0] == SCHEMA_VERSION
    finally:
        db.close()


def test_history_persists_across_connections(tmp_path: Path) -> None:
    path = tmp_path / "h.sqlite3"
    first = Database(path)
    repo = HistoryRepository(first)
    batch_id = repo.create_batch("organize", tmp_path / "Downloads")
    op_id = repo.record_operation(
        batch_id,
        OP_MOVE,
        source="/s/a.pdf",
        destination="/s/Documents/a.pdf",
        size=12,
        modified_ns=5,
        file_id="1:2",
        status=STATUS_PENDING,
    )
    repo.update_operation(op_id, STATUS_SUCCESS)
    repo.finish_batch(batch_id, "completed", "1 moved")
    first.close()

    second = Database(path)
    try:
        repo = HistoryRepository(second)
        (batch,) = repo.list_batches()
        assert batch.id == batch_id
        assert batch.root_name == "Downloads"
        assert batch.status == "completed"
        assert batch.operation_count == 1
        (operation,) = repo.list_operations(batch_id)
        assert operation.status == STATUS_SUCCESS
        assert operation.size_bytes == 12
        assert operation.file_id == "1:2"
    finally:
        second.close()


def test_totals_and_bytes_moved(history: HistoryRepository, tmp_path: Path) -> None:
    batch_id = history.create_batch("organize", tmp_path)
    history.record_operation(batch_id, OP_MOVE, source="a", destination="b", size=10, status=STATUS_SUCCESS)
    history.record_operation(batch_id, OP_MOVE, source="c", destination="d", size=5, status="failed", error="x")
    history.record_operation(batch_id, OP_MOVE, source="e", destination=None, size=7, status="skipped", error="y")
    totals = history.operation_totals()
    assert totals[STATUS_SUCCESS] == 1
    assert totals["failed"] == 1
    assert totals["skipped"] == 1
    assert history.bytes_moved() == 10


def test_dry_runs_are_excluded_from_statistics(history: HistoryRepository, tmp_path: Path) -> None:
    batch_id = history.create_batch("organize", tmp_path, dry_run=True)
    history.record_operation(batch_id, OP_MOVE, source="a", destination="b", size=1, status=STATUS_SUCCESS)
    assert history.operation_totals()[STATUS_SUCCESS] == 0


def test_interrupted_work_is_recovered_on_startup(history: HistoryRepository, tmp_path: Path) -> None:
    batch_id = history.create_batch("organize", tmp_path)
    history.record_operation(batch_id, OP_MOVE, source="a", destination="b", status=STATUS_PENDING)
    assert history.recover_interrupted() == 1
    assert history.list_operations(batch_id)[0].status == "interrupted"
    assert history.get_batch(batch_id).status == "interrupted"


def test_clear_history_keeps_user_files(history: HistoryRepository, workspace: Path) -> None:
    from .conftest import make_file

    kept = make_file(workspace / "keep.pdf")
    batch_id = history.create_batch("organize", workspace)
    history.record_operation(batch_id, OP_MOVE, source="a", destination="b", status=STATUS_SUCCESS)
    history.record_scan(
        root=workspace, file_count=1, folder_count=1, total_bytes=4, category_counts={"Documents": 1}, issue_count=0
    )
    history.clear()
    assert history.list_batches() == []
    assert history.recent_scans() == []
    assert kept.exists()


def test_scan_summaries_round_trip(history: HistoryRepository, tmp_path: Path) -> None:
    history.record_scan(
        root=tmp_path / "Downloads",
        file_count=3,
        folder_count=2,
        total_bytes=99,
        category_counts={"Images": 3},
        issue_count=1,
    )
    (scan,) = history.recent_scans()
    assert scan.root_name == "Downloads"
    assert scan.category_counts == {"Images": 3}
    assert scan.issue_count == 1


def test_corrupt_database_is_preserved_and_replaced(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    corrupt_bytes = b"this is definitely not an sqlite database" * 100
    path.write_bytes(corrupt_bytes)
    database, backup = open_database(path)
    try:
        assert backup is not None and backup.exists()
        assert backup.read_bytes() == corrupt_bytes
        assert database.integrity_ok()
    finally:
        database.close()

    # A second recovery in the same folder must use a unique backup name and preserve both.
    second_corrupt = b"second damaged database payload" * 80
    path.write_bytes(second_corrupt)
    database2, backup2 = open_database(path)
    try:
        assert backup2 is not None and backup2.exists()
        assert backup2 != backup
        assert backup.read_bytes() == corrupt_bytes
        assert backup2.read_bytes() == second_corrupt
        assert database2.integrity_ok()
    finally:
        database2.close()


def test_corrupt_database_with_wal_and_shm_companions_recovers_cleanly(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    path.write_bytes(b"corrupt main db" * 50)
    wal.write_bytes(b"corrupt wal companion" * 20)
    shm.write_bytes(b"corrupt shm companion" * 20)

    database, backup = open_database(path)
    try:
        assert backup is not None and backup.exists()
        assert backup.read_bytes() == b"corrupt main db" * 50
        assert Path(f"{backup}-wal").read_bytes() == b"corrupt wal companion" * 20
        assert Path(f"{backup}-shm").read_bytes() == b"corrupt shm companion" * 20
        assert database.integrity_ok()
    finally:
        database.close()


def test_failed_database_init_closes_connection_and_reraises(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 10}")
    conn.close()

    with pytest.raises(RuntimeError, match="newer than this application supports"):
        Database(path)

    # On Windows, renaming an open SQLite file fails with PermissionError (WinError 32).
    renamed = tmp_path / "future-renamed.sqlite3"
    os.replace(path, renamed)
    assert renamed.exists()


def test_healthy_database_is_opened_without_recovery(tmp_path: Path) -> None:
    path = tmp_path / "history.sqlite3"
    database, backup = open_database(path)
    database.close()
    assert backup is None


def test_locked_database_is_not_treated_as_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import pytest

    from smart_file_organizer.database import db as db_module

    path = tmp_path / "history.sqlite3"
    Database(path).close()

    def locked(self, _path):  # simulate "database is locked" during open
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(db_module.Database, "__init__", locked)
    with pytest.raises(sqlite3.OperationalError):
        open_database(path)
    assert path.exists()
    assert not list(tmp_path.glob("*.corrupt-*"))
