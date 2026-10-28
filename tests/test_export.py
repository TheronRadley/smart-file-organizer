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

from .conftest import make_file


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
    make_file(workspace / "Budget, 2026 - final.pdf", b"contents")
    scan = scan_folder(workspace, options, classifier)
    target = write_export(scan_export(scan), tmp_path / "scan.csv")

    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "spreadsheet applications need the byte order mark"
    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == "Budget, 2026 - final.pdf"


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
    # The parent "folder" is a file, which no platform lets us create a report inside.
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("occupied", encoding="utf-8")
    with pytest.raises(ExportError, match="Could not write"):
        write_export(history_export(history), blocker / "history.csv")
    assert blocker.read_text(encoding="utf-8") == "occupied"
    assert not list(tmp_path.glob("*.tmp")), "no partial export file may be left behind"


def test_export_never_changes_history_or_files(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    _organize(workspace, options, classifier, history)
    before = [(batch.id, batch.status) for batch in history.list_batches()]
    write_export(history_export(history), tmp_path / "history.csv")
    assert [(batch.id, batch.status) for batch in history.list_batches()] == before
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"contents"
    assert not list((tmp_path).glob("*.tmp")), "no temporary export file may be left behind"


# ----- spreadsheet formula injection (audit P0) ---------------------------


@pytest.mark.parametrize(
    "dangerous",
    [
        "=2+5+cmd|' /C calc'!A0",
        "+2+5",
        "-2+5",
        "@SUM(1+1)",
        '  =HYPERLINK("https://example.invalid")',
        "\t=1+1",
        "\r\n@1+1",
        "\x01\x1f=1+1",
        "\ufeff=1+1",
    ],
)
def test_csv_cells_starting_like_formulas_are_neutralized(tmp_path: Path, dangerous: str) -> None:
    from smart_file_organizer.core.export import Export

    export = Export(kind="scan", columns=("name",), rows=({"name": dangerous},), summary={})
    target = write_export(export, tmp_path / "scan.csv")

    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == "'" + dangerous, "the cell must be forced to text, with no characters lost"


@pytest.mark.parametrize("safe", ["normal.pdf", "a=b.pdf", "", "2026-01-01 notes.pdf"])
def test_csv_cells_that_are_not_formulas_are_unchanged(tmp_path: Path, safe: str) -> None:
    from smart_file_organizer.core.export import Export

    export = Export(kind="scan", columns=("name",), rows=({"name": safe},), summary={})
    target = write_export(export, tmp_path / "scan.csv")
    with open(target, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["name"] == safe


def test_json_export_is_not_formula_sanitized(tmp_path: Path) -> None:
    from smart_file_organizer.core.export import Export

    export = Export(kind="scan", columns=("name",), rows=({"name": "=1+1"},), summary={})
    target = write_export(export, tmp_path / "scan.json")
    assert json.loads(target.read_text(encoding="utf-8"))["rows"][0]["name"] == "=1+1"


# ----- no silent overwrite (audit P0) --------------------------------------


def test_existing_export_target_is_never_replaced_silently(tmp_path: Path, history: HistoryRepository) -> None:
    target = tmp_path / "history.csv"
    target.write_text("previous report", encoding="utf-8")
    with pytest.raises(ExportError, match="already exists"):
        write_export(history_export(history), target)
    assert target.read_text(encoding="utf-8") == "previous report"


def test_explicit_overwrite_replaces_the_target(tmp_path: Path, history: HistoryRepository) -> None:
    target = tmp_path / "history.csv"
    target.write_text("previous report", encoding="utf-8")
    write_export(history_export(history), target, overwrite=True)
    assert "previous report" not in target.read_text(encoding="utf-8-sig")
    assert not list(tmp_path.glob("*.tmp"))


def test_target_created_during_the_write_is_not_replaced(
    tmp_path: Path, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import export as export_module

    target = tmp_path / "history.csv"
    real_write_csv = export_module._write_csv

    def write_then_compete(export, path) -> None:
        real_write_csv(export, path)
        target.write_text("written by another process", encoding="utf-8")

    monkeypatch.setattr(export_module, "_write_csv", write_then_compete)
    with pytest.raises(ExportError, match="already exists"):
        write_export(history_export(history), target)
    assert target.read_text(encoding="utf-8") == "written by another process"
    assert not list(tmp_path.glob("*.tmp")), "no temporary export file may be left behind"


def test_partial_copy_during_publication_is_removed_and_reported(
    tmp_path: Path, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import export as export_module

    def fail_link(source, destination, *args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    def copy_halfway(source_handle, destination_handle, *args, **kwargs):
        destination_handle.write(b"partial report")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(export_module.os, "link", fail_link)
    monkeypatch.setattr(export_module.shutil, "copyfileobj", copy_halfway)
    target = tmp_path / "history.csv"
    with pytest.raises(ExportError, match="Could not write"):
        write_export(history_export(history), target)
    assert not target.exists(), "an incomplete report must not be left at the target"
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp")), (
        "no temporary or partial export file may be left behind"
    )
