"""Exports for auditing and recovery: history, scan results and duplicate reports.

An export reads the report data and writes it to the user's chosen path. It
does not update history or settings.

CSV files are written as UTF-8 with a byte order mark, because spreadsheet
applications otherwise assume a legacy encoding and mangle non-ASCII paths.
JSON keeps the same rows plus a summary block.

CSV cells are sanitized against spreadsheet formula injection: a cell whose
first non-whitespace/control character is ``=``, ``+``, ``-`` or ``@`` is
prefixed with an apostrophe so Excel/LibreOffice treat it as text, not a
formula. CSV quoting alone would not prevent that. JSON is not sanitized,
because it is not opened as a spreadsheet.

New targets are always published without clobbering an existing file. An
explicit replacement is tied to a snapshot of the existing regular file, which
is rechecked before publishing the report.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from smart_file_organizer.core.boundary import is_reparse_entry, storage_identity
from smart_file_organizer.core.duplicates import DuplicateReport
from smart_file_organizer.core.fileops import rename_no_replace
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanResult
from smart_file_organizer.utils.paths import app_data_dir, is_within

logger = logging.getLogger(__name__)

CSV_SUFFIX = ".csv"
JSON_SUFFIX = ".json"
EXPORT_SUFFIXES = (CSV_SUFFIX, JSON_SUFFIX)
UTF8_BOM = "utf-8-sig"

HISTORY_COLUMNS = (
    "batch_id",
    "batch_kind",
    "batch_status",
    "root",
    "started",
    "finished",
    "operation",
    "source",
    "destination",
    "size_bytes",
    "status",
    "error",
    "undo_status",
    "undo_error",
)

SCAN_COLUMNS = (
    "relative_path",
    "name",
    "extension",
    "category",
    "size_bytes",
    "modified",
    "depth",
    "top_folder",
)

DUPLICATE_COLUMNS = (
    "digest",
    "size_bytes",
    "path",
    "modified",
    "file_id",
    "copies_in_group",
    "recoverable_bytes_in_group",
)


class ExportError(ValueError):
    """The export could not be written."""


@dataclass(frozen=True)
class Export:
    """Tabular rows plus a summary, ready to be written as CSV or JSON."""

    kind: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    summary: dict[str, Any]


def history_export(history: HistoryRepository, *, limit: int | None = None) -> Export:
    """One row per recorded operation, newest batch first."""
    batches = history.list_batches(limit)
    rows: list[dict[str, Any]] = []
    for batch in batches:
        for operation in history.list_operations(batch.id):
            rows.append(
                {
                    "batch_id": batch.id,
                    "batch_kind": batch.kind,
                    "batch_status": batch.status,
                    "root": batch.root_path,
                    "started": _timestamp(batch.created_at),
                    "finished": _timestamp(batch.finished_at),
                    "operation": operation.op_type,
                    "source": operation.source_path or "",
                    "destination": operation.destination_path or "",
                    "size_bytes": operation.size_bytes if operation.size_bytes is not None else "",
                    "status": operation.status,
                    "error": operation.error or "",
                    "undo_status": operation.undo_status or "",
                    "undo_error": operation.undo_error or "",
                }
            )
    return Export(
        kind="history",
        columns=HISTORY_COLUMNS,
        rows=tuple(rows),
        summary={"batches": len(batches), "operations": len(rows)},
    )


def scan_export(scan: ScanResult) -> Export:
    """One row per scanned file, plus the issues that were collected."""
    rows = tuple(
        {
            "relative_path": entry.relative_path,
            "name": entry.name,
            "extension": entry.extension,
            "category": entry.category,
            "size_bytes": entry.size,
            "modified": _timestamp(entry.modified_ns / 1_000_000_000),
            "depth": entry.depth,
            "top_folder": entry.top_folder or "",
        }
        for entry in scan.entries
    )
    return Export(
        kind="scan",
        columns=SCAN_COLUMNS,
        rows=rows,
        summary={
            "root": str(scan.root),
            "files": scan.file_count,
            "folders": scan.folders_scanned,
            "total_bytes": scan.total_bytes,
            "cancelled": scan.cancelled,
            "issues": [{"path": issue.path, "message": issue.message} for issue in scan.issues],
        },
    )


def duplicates_export(report: DuplicateReport) -> Export:
    """One row per file in a duplicate group, largest recoverable groups first."""
    rows: list[dict[str, Any]] = []
    for group in report.groups:
        for duplicate in group.files:
            rows.append(
                {
                    "digest": group.digest,
                    "size_bytes": group.size,
                    "path": str(duplicate.path),
                    "modified": _timestamp(duplicate.modified_ns / 1_000_000_000),
                    "file_id": duplicate.file_id or "",
                    "copies_in_group": len(group.files),
                    "recoverable_bytes_in_group": group.recoverable_bytes,
                }
            )
    return Export(
        kind="duplicates",
        columns=DUPLICATE_COLUMNS,
        rows=tuple(rows),
        summary={
            "root": str(report.root),
            "groups": len(report.groups),
            "duplicate_files": report.duplicate_file_count,
            "recoverable_bytes": report.recoverable_bytes,
            "files_examined": report.files_examined,
            "excluded_changed": report.excluded_changed,
            "cancelled": report.cancelled,
        },
    )


@dataclass(frozen=True)
class ExportTarget:
    path: Path
    signature: tuple[int, int, int, int, int]


def inspect_export_target(path: Path | str) -> ExportTarget | None:
    """Capture the exact file that would be replaced, or None for a new path."""
    path = Path(path).absolute()
    if is_within(path, app_data_dir()):
        raise ExportError("Choose a report path outside the application's data folder.")
    try:
        stats = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ExportError(f"Could not write to '{path}': {exc.strerror or exc}") from exc
    if not stat.S_ISREG(stats.st_mode) or is_reparse_entry(stats):
        kind = "a folder" if stat.S_ISDIR(stats.st_mode) else "a link or special file"
        raise ExportError(f"'{path}' is {kind}. Choose a regular file name to write the report to.")
    if not stats.st_ino:
        raise ExportError(f"Cannot verify the identity of '{path}'. Choose a new export name.")
    return ExportTarget(path, (stats.st_dev, stats.st_ino, stats.st_size, stats.st_mtime_ns, stats.st_ctime_ns))


def write_export(
    export: Export, path: Path | str, *, overwrite: bool = False, replace_existing: ExportTarget | None = None
) -> Path:
    """Publish a report without clobbering an unapproved target.

    UI callers pass a snapshot captured *before* explicit replacement consent.
    ``overwrite=True`` is for non-interactive callers that authorize replacing
    the file present at entry. It can never authorize a newly appearing file.
    """
    chosen = Path(path)
    target = chosen.absolute()
    suffix = target.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ExportError(f"'{target.name}' is not a supported export file. Choose a name ending in .csv or .json.")
    existing = inspect_export_target(target)
    if replace_existing is None and overwrite:
        replace_existing = existing
    if replace_existing is not None:
        if existing != replace_existing:
            raise ExportError(f"'{target.name}' has changed since you chose it. Nothing was replaced.")
    elif existing is not None:
        raise ExportError(
            f"'{target.name}' already exists and was not changed. Choose another name, or confirm overwriting it."
        )
    temporary: Path | None = None
    temporary_id: str | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # A uniquely named temporary in the target folder: concurrent exports to
        # the same target cannot corrupt each other through a shared ".tmp" file.
        descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        temporary = Path(temporary_name)
        encoding = UTF8_BOM if suffix == CSV_SUFFIX else "utf-8"
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as stream:
            temporary_id = storage_identity(os.fstat(stream.fileno()))
            if suffix == CSV_SUFFIX:
                _write_csv(export, stream)
            else:
                _write_json(export, stream)
        if not _is_owned_temporary(temporary, temporary_id):
            raise ExportError(f"The temporary report '{temporary}' could not be verified; left in place.")
        if is_within(target, app_data_dir()):
            raise ExportError("Choose a report path outside the application's data folder.")
        if replace_existing is not None:
            try:
                still_approved = inspect_export_target(target) == replace_existing
            except ExportError:
                still_approved = False
            if not still_approved:
                raise ExportError(f"'{target.name}' changed while writing the report. Nothing was replaced.")
            os.replace(temporary, target)
            temporary = None
        else:
            try:
                rename_no_replace(temporary, target)
            except FileExistsError as exc:
                raise ExportError(
                    f"'{target.name}' already exists and was not changed. "
                    "Choose another name, or confirm overwriting it."
                ) from exc
            temporary = None
    except ExportError:
        raise
    except OSError as exc:
        # Cleaning up must never mask the real reason the export failed.
        raise ExportError(f"Could not write '{target}': {exc.strerror or exc}") from exc
    finally:
        if temporary is not None:
            with suppress(OSError):
                if _is_owned_temporary(temporary, temporary_id):
                    temporary.unlink()
    logger.info("Exported %d %s row(s) to %s", len(export.rows), export.kind, target)
    return chosen


def _is_owned_temporary(path: Path, expected: str | None) -> bool:
    if expected is None or is_within(path, app_data_dir()):
        return False
    stats = path.lstat()
    return stat.S_ISREG(stats.st_mode) and not is_reparse_entry(stats) and storage_identity(stats) == expected


def _write_csv(export: Export, stream: IO[str]) -> None:
    writer = csv.DictWriter(stream, fieldnames=list(export.columns), extrasaction="ignore", quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows({column: _csv_cell(row.get(column, "")) for column in export.columns} for row in export.rows)


#: Characters a spreadsheet treats as the start of a formula in the first cell position.
_FORMULA_STARTERS = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> Any:
    """Neutralize a string cell that a spreadsheet would execute as a formula.

    Detection skips leading whitespace and control characters (including tab
    and newline tricks); the apostrophe is prefixed to the original value so
    no filename characters are lost. Non-strings (sizes, counts) pass through.
    """
    if not isinstance(value, str) or not value:
        return value
    stripped = value.lstrip("".join(chr(code) for code in range(0x21)) + "\u00ad\ufeff")
    if stripped[:1] in _FORMULA_STARTERS:
        return "'" + value
    return value


def _write_json(export: Export, stream: IO[str]) -> None:
    document = {
        "kind": export.kind,
        "generated_at": _timestamp(datetime.now().timestamp()),
        "summary": export.summary,
        "rows": list(export.rows),
    }
    json.dump(document, stream, indent=2, ensure_ascii=False)
    stream.write("\n")


def _timestamp(value: float | None) -> str:
    """Local ISO-8601 text for a report; empty when there is nothing to show."""
    if value is None:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")
