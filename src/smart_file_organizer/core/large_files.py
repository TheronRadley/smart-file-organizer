"""Largest-file discovery built on top of a completed scan."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from smart_file_organizer.core.models import FileEntry, ScanIssue, ScanResult


@dataclass(frozen=True)
class LargeFileReport:
    root: Path
    min_bytes: int
    files: tuple[FileEntry, ...]
    issues: tuple[ScanIssue, ...]
    cancelled: bool

    @property
    def incomplete(self) -> bool:
        """True when some files could not be read or the scan was cancelled."""
        return self.cancelled or bool(self.issues)


def find_large_files(
    scan: ScanResult,
    *,
    min_bytes: int,
    extensions: Iterable[str] = (),
    limit: int | None = None,
) -> LargeFileReport:
    """Return files of at least ``min_bytes``, largest first, optionally filtered by extension."""
    wanted = {extension.lower().lstrip(".") for extension in extensions if extension.strip()}
    matches = [entry for entry in scan.entries if entry.size >= min_bytes and (not wanted or entry.extension in wanted)]
    matches.sort(key=lambda entry: (-entry.size, entry.relative_path.casefold()))
    if limit is not None:
        matches = matches[:limit]
    return LargeFileReport(
        root=scan.root,
        min_bytes=min_bytes,
        files=tuple(matches),
        issues=tuple(scan.issues),
        cancelled=scan.cancelled,
    )
