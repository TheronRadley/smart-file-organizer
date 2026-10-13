"""Exports are read-only reports: their contents and their failure modes."""

import csv
import json
from pathlib import Path

import pytest

from smart_file_organizer.core.duplicates import find_duplicates
from smart_file_organizer.core.export import (
    ExportError,
    duplicates_export,
    history_export,
    scan_export,
    write_export,
)
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import BATCH_ORGANIZE, OP_MOVE, STATUS_SUCCESS, ScanOptions
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import scan_folder

from .conftest import is_root_user, make_file


def _organize(workspace: Path, options: ScanOptions, classifier, history: HistoryRepository) -> str:
    make_file(workspace / "report.pdf", b"contents")
    plan = build_plan(scan_folder(workspace, options), classifier)
    outcome = execute_plan(plan, history=history)
    assert outcome.batch_id is not None
    return outcome.batch_id


def test_history_export_csv_has_one_row_per_operation(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    _organize(workspace, options, classifier, history)
    target = write_export(history_export(history), tmp_path / "history.csv")

    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2  # create_folder + move
    move = next(row for row in rows if row["operation"] == OP_MOVE)
    assert move["status"] == STATUS_SUCCESS
    assert move["source"].endswith("report.pdf")
    assert move["destination"].endswith(str(Path("Documents") / "report.pdf"))
    assert move["batch_kind"] == BATCH_ORGANIZE


def test_history_export_json_round_trips(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    _organize(workspace, options, classifier, history)
    target = write_export(history_export(history), tmp_path / "history.json")

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["kind"] == "history"
    assert document["summary"]["batches"] == 1
    assert document["summary"]["operations"] == 2
    assert {row["operation"] for row in document["rows"]} == {"move", "create_folder"}


def test_csv_is_utf8_with_a_bom_and_quotes_commas(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    # A path containing a comma and non-ASCII characters must survive the round trip.
    make_file(workspace / "Budget, 2026 — final.pdf", b"contents")
    scan = scan_folder(workspace, options, classifier)
    target = write_export(scan_export(scan), tmp_path / "scan.csv")

    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "spreadsheet applications need the byte order mark"
    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == "Budget, 2026 — final.pdf"


def test_scan_export_lists_every_file(
    workspace: Path, recursive_options: ScanOptions, classifier, tmp_path: Path
) -> None:
    make_file(workspace / "one" / "a.pdf", b"1")
    make_file(workspace / "two" / "b.mp3", b"22")
    scan = scan_folder(workspace, recursive_options, classifier)
    target = write_export(scan_export(scan), tmp_path / "scan.json")

    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["summary"]["files"] == 2
    assert {row["category"] for row in document["rows"]} == {"Documents", "Audio"}
    assert {row["top_folder"] for row in document["rows"]} == {"one", "two"}


def test_duplicates_export_reports_recoverable_bytes(
    workspace: Path, recursive_options: ScanOptions, tmp_path: Path
) -> None:
    make_file(workspace / "a.bin", b"same content")
    make_file(workspace / "b.bin", b"same content")
    report = find_duplicates(workspace, recursive_options)
    target = write_export(duplicates_export(report), tmp_path / "duplicates.csv")

    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert {row["copies_in_group"] for row in rows} == {"2"}
    assert {row["recoverable_bytes_in_group"] for row in rows} == {str(len(b"same content"))}
    assert len({row["digest"] for row in rows}) == 1


def test_unsupported_extension_is_refused(tmp_path: Path, history: HistoryRepository) -> None:
    with pytest.raises(ExportError, match=r"\.csv or \.json"):
        write_export(history_export(history), tmp_path / "history.xlsx")


def test_a_folder_as_target_is_refused_rather_than_replaced(tmp_path: Path, history: HistoryRepository) -> None:
    folder = tmp_path / "reports.csv"  # a folder whose name looks like an export file
    folder.mkdir()
    (folder / "keep.txt").write_text("existing", encoding="utf-8")
    with pytest.raises(ExportError, match="is a folder"):
        write_export(history_export(history), folder)
    assert (folder / "keep.txt").read_text(encoding="utf-8") == "existing"


def test_an_unwritable_target_is_reported_clearly(tmp_path: Path, history: HistoryRepository) -> None:
    if is_root_user():
        pytest.skip("Write permissions are not enforced for the root user")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)  # readable, not writable
    try:
        with pytest.raises(ExportError, match="Could not write"):
            write_export(history_export(history), locked / "history.csv")
        assert not list(locked.iterdir()), "no partial export file may be left behind"
    finally:
        locked.chmod(0o700)


def test_export_never_changes_history_or_files(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    _organize(workspace, options, classifier, history)
    before = [(batch.id, batch.status) for batch in history.list_batches()]
    write_export(history_export(history), tmp_path / "history.csv")
    assert [(batch.id, batch.status) for batch in history.list_batches()] == before
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"contents"
    assert not list((tmp_path).glob("*.tmp")), "no temporary export file may be left behind"
