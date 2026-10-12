"""Folder scanning with cancellation, exclusions and graceful error handling.

The scanner never follows symbolic links or Windows junctions, and it verifies
that every directory it descends into still resolves inside the selected root.
"""

from __future__ import annotations

import fnmatch
import logging
import os
from collections.abc import Callable
from pathlib import Path
from threading import Event

from smart_file_organizer.core.classifier import Classifier, extension_of
from smart_file_organizer.core.models import (
    FileEntry,
    ScanIssue,
    ScanOptions,
    ScanProgress,
    ScanResult,
)
from smart_file_organizer.utils.paths import is_link_like, is_within

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[ScanProgress], None]
PROGRESS_INTERVAL = 200


class ScanError(OSError):
    """The selected folder cannot be scanned at all."""


def scan_folder(
    root: Path | str,
    options: ScanOptions,
    classifier: Classifier | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancel_event: Event | None = None,
) -> ScanResult:
    """Scan ``root`` and return every regular file found.

    Raises :class:`ScanError` when the root is missing, not a directory, or a
    symbolic link. Errors inside the tree are collected as ``issues`` instead.
    """
    root_path = Path(os.path.abspath(os.fspath(root)))
    if is_link_like(root_path):
        raise ScanError(f"'{root_path}' is a symbolic link. Choose the real folder instead.")
    if not root_path.is_dir():
        raise ScanError(f"'{root_path}' is not an existing folder.")

    active_classifier = classifier or Classifier()
    excluded_folders = {name.casefold() for name in options.excluded_folders}
    patterns = tuple(pattern.casefold() for pattern in options.excluded_patterns)

    result = ScanResult(
        root=root_path,
        options=options,
        entries=[],
        folders_scanned=1,
        skipped_links=0,
        skipped_excluded=0,
        skipped_other=0,
        issues=[],
    )

    pending_dirs: list[tuple[Path, str]] = [(root_path, "")]
    while pending_dirs:
        directory, prefix = pending_dirs.pop()
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
        except OSError as exc:
            result.issues.append(ScanIssue(str(directory), describe_os_error(exc)))
            continue

        for child in children:
            if cancel_event is not None and cancel_event.is_set():
                result.cancelled = True
                return result
            relative = f"{prefix}{child.name}"
            child_path = Path(child.path)

            if _is_link(child):
                result.skipped_links += 1
                continue

            if child.is_dir(follow_symlinks=False):
                if child.name.casefold() in excluded_folders or _matches(child.name, patterns):
                    result.skipped_excluded += 1
                    continue
                if not is_within(child_path, root_path):
                    # Defence in depth: a reparse point we failed to recognise.
                    result.skipped_links += 1
                    continue
                result.folders_scanned += 1
                if options.recursive:
                    pending_dirs.append((child_path, f"{relative}/"))
                continue

            if child.is_file(follow_symlinks=False):
                if _matches(child.name, patterns):
                    result.skipped_excluded += 1
                    continue
                try:
                    stats = child.stat(follow_symlinks=False)
                except OSError as exc:
                    result.issues.append(ScanIssue(str(child_path), describe_os_error(exc)))
                    continue
                result.entries.append(_make_entry(child_path, relative, child.name, stats, active_classifier))
                if progress is not None and len(result.entries) % PROGRESS_INTERVAL == 0:
                    progress(ScanProgress(len(result.entries), result.folders_scanned, str(child_path)))
                continue

            result.skipped_other += 1

    result.entries.sort(key=lambda entry: entry.relative_path.casefold())
    if progress is not None:
        progress(ScanProgress(len(result.entries), result.folders_scanned, str(root_path)))
    logger.info(
        "Scanned %s: %d files, %d folders, %d issues",
        root_path.name,
        len(result.entries),
        result.folders_scanned,
        len(result.issues),
    )
    return result


def describe_os_error(exc: OSError) -> str:
    """User-facing description of a filesystem error (no stack traces)."""
    if isinstance(exc, PermissionError):
        return "Access denied"
    if isinstance(exc, FileNotFoundError):
        return "No longer exists"
    return exc.strerror or "Unknown filesystem error"


def _is_link(child: os.DirEntry[str]) -> bool:
    if child.is_symlink():
        return True
    is_junction = getattr(child, "is_junction", None)
    return bool(is_junction and is_junction())


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern) for pattern in patterns)


def _make_entry(path: Path, relative: str, name: str, stats: os.stat_result, classifier: Classifier) -> FileEntry:
    creation: float | None
    if hasattr(stats, "st_birthtime"):
        creation = stats.st_birthtime
    elif os.name == "nt":
        creation = stats.st_ctime  # On Windows st_ctime is the creation time.
    else:
        creation = None
    return FileEntry(
        path=path,
        relative_path=relative,
        name=name,
        extension=extension_of(name),
        size=stats.st_size,
        modified_ns=stats.st_mtime_ns,
        created_ts=creation,
        file_id=file_identity(stats),
        category=classifier.category_for_name(name),
    )


def file_identity(stats: os.stat_result) -> str | None:
    """Stable identifier for a file's storage object, used to detect replacement."""
    if not stats.st_ino:
        return None
    return f"{stats.st_dev}:{stats.st_ino}"
