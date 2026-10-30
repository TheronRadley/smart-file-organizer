"""Exports for auditing and recovery: history, scan results and duplicate reports.

Everything here is read-only. An export never changes history and never touches
the files that were organized; it only writes a report to a path the user
chooses.

CSV files are written as UTF-8 with a byte order mark, because spreadsheet
applications otherwise assume a legacy encoding and mangle non-ASCII paths.
JSON keeps the same rows plus a summary block.

CSV cells are sanitized against spreadsheet formula injection: a cell whose
first non-whitespace/control character is ``=``, ``+``, ``-`` or ``@`` is
prefixed with an apostrophe so Excel/LibreOffice treat it as text, not a
formula. CSV quoting alone would not prevent that. JSON is not sanitized,
because it is not opened as a spreadsheet.

Exports never silently replace an existing file. ``overwrite`` must be passed
explicitly (the save dialog's own overwrite prompt is that confirmation in the
UI); the report is written to a uniquely named temporary file in the target
folder and published without clobbering a file that appears in the meantime.
"""

from __future__ import annotations

import csv
import errno
import json
import logging
import os
import shutil
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from smart_file_organizer.core.duplicates import DuplicateReport
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanResult

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


def write_export(export: Export, path: Path | str, *, overwrite: bool = False) -> Path:
    """Write ``export`` to ``path``; the format comes from the file extension.

    An existing target is never replaced silently: without ``overwrite=True``
    the write is refused, including when another process creates the target
    while the report is being written. Pass ``overwrite=True`` only after the
    user has explicitly confirmed replacing that exact file.
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ExportError(f"'{target.name}' is not a supported export file. Choose a name ending in .csv or .json.")
    if target.is_dir():
        # os.replace() would happily swap an empty folder for the report file.
        raise ExportError(f"'{target}' is a folder. Choose a file name to write the report to.")
    if not overwrite and os.path.lexists(target):
        raise ExportError(
            f"'{target.name}' already exists and was not changed. Choose another name, or confirm overwriting it."
        )
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # A uniquely named temporary in the target folder: concurrent exports to
        # the same target cannot corrupt each other through a shared ".tmp" file.
        descriptor, temporary_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        os.close(descriptor)
        temporary = Path(temporary_name)
        if suffix == CSV_SUFFIX:
            _write_csv(export, temporary)
        else:
            _write_json(export, temporary)
        if overwrite:
            os.replace(temporary, target)
            temporary = None
        else:
            try:
                _publish_no_overwrite(temporary, target)
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
                temporary.unlink(missing_ok=True)
    logger.info("Exported %d %s row(s) to %s", len(export.rows), export.kind, target)
    return target


def _is_same_export_file(current: os.stat_result, created: os.stat_result) -> bool:
    """Storage-identity check so cleanup never deletes a replacement file.

    Device+inode alone is not enough (a replacement can reuse the inode), so
    the creation/change time reported by the file's own handle must match too.
    """
    if not current.st_ino or not created.st_ino:
        return False
    return (current.st_dev, current.st_ino, current.st_ctime_ns) == (
        created.st_dev,
        created.st_ino,
        created.st_ctime_ns,
    )


def _publish_no_overwrite(temporary: Path, target: Path) -> None:
    """Publish ``temporary`` as ``target`` only if ``target`` is still free.

    A hard link is atomic: it fails with :class:`FileExistsError` instead of
    replacing a file another process created meanwhile. Filesystems without
    hard links fall back to an exclusive-create copy, which fails the same way.
    """
    try:
        os.link(temporary, target)
    except FileExistsError:
        raise
    except OSError:
        # Exclusive-create copy. If the copy fails halfway (disk full, target
        # removed), the incomplete destination this call created is removed:
        # a partial report must never sit at the target looking complete.
        destination_handle = None
        try:
            with open(temporary, "rb") as source:
                destination_handle = open(target, "xb")  # noqa: SIM115 - closed explicitly below (identity needed)
                try:
                    shutil.copyfileobj(source, destination_handle)
                finally:
                    # Identity of the file WE created, read from its own handle
                    # at failure time. If the target path was replaced during
                    # copying, the path now names a different file: never delete it.
                    try:
                        created_stats: os.stat_result | None = os.fstat(destination_handle.fileno())
                    except (OSError, ValueError):
                        created_stats = None
                    destination_handle.close()
                    destination_handle = None
        except BaseException:
            if created_stats is not None:
                try:
                    current = os.lstat(target)
                except OSError:
                    current = None
                if current is not None and _is_same_export_file(current, created_stats):
                    try:
                        target.unlink()
                    except OSError as cleanup_exc:
                        raise OSError(
                            errno.EIO,
                            f"Incomplete export could not be removed; delete '{target}' manually",
                        ) from cleanup_exc
                elif current is not None:
                    raise OSError(
                        errno.EIO,
                        f"Incomplete export at '{target}' was replaced during copying and left in place",
                    ) from None
            raise
        finally:
            if destination_handle is not None:
                destination_handle.close()
    temporary.unlink()


def _write_csv(export: Export, path: Path) -> None:
    with open(path, "w", encoding=UTF8_BOM, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(export.columns), extrasaction="ignore", quoting=csv.QUOTE_ALL)
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


def _write_json(export: Export, path: Path) -> None:
    document = {
        "kind": export.kind,
        "generated_at": _timestamp(datetime.now().timestamp()),
        "summary": export.summary,
        "rows": list(export.rows),
    }
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _timestamp(value: float | None) -> str:
    """Local ISO-8601 text for a report; empty when there is nothing to show."""
    if value is None:
        return ""
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")
