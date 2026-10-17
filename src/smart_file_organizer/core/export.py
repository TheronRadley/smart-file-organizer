"""Exports for auditing and recovery: history, scan results and duplicate reports.

Everything here is read-only. An export never changes history and never touches
the files that were organized; it only writes a report to a path the user
chooses.

CSV files are written as UTF-8 with a byte order mark, because spreadsheet
applications otherwise assume a legacy encoding and mangle non-ASCII paths.
JSON keeps the same rows plus a summary block.
"""

from __future__ import annotations

import csv
import json
import logging
import os
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


def history_export(history: HistoryRepository, *, limit: int = 200) -> Export:
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


def write_export(export: Export, path: Path | str) -> Path:
    """Write ``export`` to ``path``; the format comes from the file extension."""
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in EXPORT_SUFFIXES:
        raise ExportError(f"'{target.name}' is not a supported export file. Choose a name ending in .csv or .json.")
    if target.is_dir():
        # os.replace() would happily swap an empty folder for the report file.
        raise ExportError(f"'{target}' is a folder. Choose a file name to write the report to.")
    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if suffix == CSV_SUFFIX:
            _write_csv(export, temporary)
        else:
            _write_json(export, temporary)
        os.replace(temporary, target)
    except OSError as exc:
        # Cleaning up must never mask the real reason the export failed.
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
        raise ExportError(f"Could not write '{target}': {exc.strerror or exc}") from exc
    logger.info("Exported %d %s row(s) to %s", len(export.rows), export.kind, target)
    return target


def _write_csv(export: Export, path: Path) -> None:
    with open(path, "w", encoding=UTF8_BOM, newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(export.columns), extrasaction="ignore", quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(export.rows)


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
