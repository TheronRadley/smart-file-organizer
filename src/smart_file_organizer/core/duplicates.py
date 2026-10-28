"""Duplicate detection by size, partial hash and full SHA-256 content hash.

Files are never compared by name. Two files are grouped only when their full
SHA-256 digests match. Files whose size, timestamp or identity changes while
they are being hashed are excluded rather than reported with stale data.
Duplicates are reported only; nothing is deleted.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from smart_file_organizer.core.fileops import open_read_no_follow
from smart_file_organizer.core.models import FileEntry, ScanIssue, ScanOptions
from smart_file_organizer.core.scanner import (
    describe_os_error,
    file_identity,
    scan_folder,
)

logger = logging.getLogger(__name__)

PARTIAL_HASH_BYTES = 64 * 1024
READ_CHUNK_BYTES = 1024 * 1024
ProgressCallback = Callable[[int, int], None]


class _ChangedDuringScan(Exception):
    """Internal signal: a file changed while it was being hashed."""


@dataclass(frozen=True)
class DuplicateFile:
    path: Path
    size: int
    modified_ns: int
    file_id: str | None


@dataclass(frozen=True)
class DuplicateGroup:
    digest: str
    size: int
    files: tuple[DuplicateFile, ...]

    @property
    def distinct_copies(self) -> int:
        """Number of separate storage objects; hard links to one file count once."""
        return len({file.file_id or str(file.path) for file in self.files})

    @property
    def recoverable_bytes(self) -> int:
        return self.size * max(0, self.distinct_copies - 1)


@dataclass(frozen=True)
class DuplicateReport:
    root: Path
    groups: tuple[DuplicateGroup, ...]
    files_examined: int
    excluded_changed: int
    issues: tuple[ScanIssue, ...]
    cancelled: bool

    @property
    def recoverable_bytes(self) -> int:
        return sum(group.recoverable_bytes for group in self.groups)

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(group.files) for group in self.groups)


def find_duplicates(
    root: Path | str,
    options: ScanOptions,
    *,
    min_size: int = 1,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> DuplicateReport:
    """Find files under ``root`` whose contents are identical."""
    scan = scan_folder(root, options, cancel_event=cancel_event)
    issues = list(scan.issues)
    if scan.cancelled:
        return DuplicateReport(scan.root, (), 0, 0, tuple(issues), cancelled=True)

    by_size: dict[int, list[FileEntry]] = defaultdict(list)
    for entry in scan.entries:
        if entry.size >= max(min_size, 1):
            by_size[entry.size].append(entry)
    size_buckets = [bucket for bucket in by_size.values() if len(bucket) > 1]
    total_steps = sum(len(bucket) for bucket in size_buckets)

    groups: list[DuplicateGroup] = []
    examined = 0
    excluded = 0
    cancelled = False

    def report_progress() -> None:
        if progress is not None:
            progress(examined, total_steps)

    for bucket in size_buckets:
        partial: dict[str, list[FileEntry]] = defaultdict(list)
        for entry in bucket:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            try:
                digest = _hash_or_none(entry, PARTIAL_HASH_BYTES, issues, cancel_event)
            except _HashCancelled:
                cancelled = True
                break
            examined += 1
            report_progress()
            if digest is None:
                excluded += 1
                continue
            partial[digest].append(entry)
        if cancelled:
            break

        for candidates in partial.values():
            if len(candidates) < 2:
                continue
            full: dict[str, list[tuple[FileEntry, str]]] = defaultdict(list)
            for entry in candidates:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    if entry.size <= PARTIAL_HASH_BYTES:
                        digest = _hash_or_none(entry, PARTIAL_HASH_BYTES, issues, cancel_event)
                    else:
                        digest = _hash_or_none(entry, None, issues, cancel_event)
                except _HashCancelled:
                    cancelled = True
                    break
                if digest is None:
                    excluded += 1
                    continue
                full[digest].append((entry, digest))
            if cancelled:
                break
            for digest, members in full.items():
                if len(members) < 2:
                    continue
                files = tuple(
                    DuplicateFile(entry.path, entry.size, entry.modified_ns, entry.file_id) for entry, _ in members
                )
                groups.append(DuplicateGroup(digest=digest, size=members[0][0].size, files=files))
        if cancelled:
            break

    groups.sort(key=lambda group: (-group.recoverable_bytes, group.digest))
    logger.info("Duplicate scan of %s found %d group(s)", scan.root.name, len(groups))
    return DuplicateReport(
        root=scan.root,
        groups=tuple(groups),
        files_examined=examined,
        excluded_changed=excluded,
        issues=tuple(issues),
        cancelled=cancelled,
    )


class _HashCancelled(Exception):
    """Cancellation arrived while a large file was being hashed."""


def _hash_or_none(
    entry: FileEntry, limit: int | None, issues: list[ScanIssue], cancel_event: Event | None = None
) -> str | None:
    """Hash a file, recording (rather than raising) problems. Returns None when excluded."""
    try:
        return hash_file(entry, limit, cancel_event=cancel_event)
    except _HashCancelled:
        raise
    except _ChangedDuringScan:
        issues.append(ScanIssue(str(entry.path), "Changed while being compared; excluded"))
    except OSError as exc:
        issues.append(ScanIssue(str(entry.path), describe_os_error(exc)))
    return None


def hash_file(entry: FileEntry, limit: int | None = None, *, cancel_event: Event | None = None) -> str:
    """SHA-256 of the first ``limit`` bytes (or the whole file when ``limit`` is None).

    The file is opened without following a symbolic link and read through that
    handle, so a link swapped in after the scan cannot redirect the read to a
    different file. The handle is compared with the size, timestamp and
    identity the scanner recorded, and again once reading finished, so a file
    that changed is excluded rather than reported with stale data.

    Raises ``_ChangedDuringScan`` if this is not the file that was scanned.
    """
    handle, stats = open_read_no_follow(entry.path)
    with handle:
        if not stat.S_ISREG(stats.st_mode) or not _matches_scan(stats, entry):
            raise _ChangedDuringScan()
        digest = hashlib.sha256()
        remaining = limit
        while remaining is None or remaining > 0:
            if cancel_event is not None and cancel_event.is_set():
                # Checked per chunk, not per file, so a multi-gigabyte file on a
                # slow disk cannot delay cancellation until it finishes.
                raise _HashCancelled()
            request = READ_CHUNK_BYTES if remaining is None else min(READ_CHUNK_BYTES, remaining)
            chunk = handle.read(request)
            if not chunk:
                break
            digest.update(chunk)
            if remaining is not None:
                remaining -= len(chunk)
        after = os.fstat(handle.fileno())
    if not _matches_scan(after, entry):
        raise _ChangedDuringScan()
    return digest.hexdigest()


def _matches_scan(stats: os.stat_result, entry: FileEntry) -> bool:
    """True when ``stats`` still describes the file the scanner recorded."""
    if stats.st_size != entry.size or stats.st_mtime_ns != entry.modified_ns:
        return False
    return entry.file_id is None or file_identity(stats, entry.path) == entry.file_id
