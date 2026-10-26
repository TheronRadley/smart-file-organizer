"""Folder scanning with cancellation, exclusions and graceful error handling.

The scanner never follows symbolic links or Windows junctions, and it verifies
that every directory it descends into still resolves inside the selected root.
"""

from __future__ import annotations

import ctypes
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
from smart_file_organizer.utils.paths import is_link_like, is_within, protected_root_reason

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
    protected = protected_root_reason(root_path)
    if protected is not None:
        raise ScanError(
            f"'{root_path}' is {protected}, which is protected. "
            "Choose a user folder such as Documents or Downloads instead."
        )
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
        file_id=file_identity(stats, path),
        category=classifier.category_for_name(name),
    )


def file_identity(stats: os.stat_result, path: Path | str | None = None) -> str | None:
    """Stable identifier for a file's storage object, used to detect replacement.

    On POSIX and on Windows ``os.lstat()`` results, ``st_dev`` and ``st_ino``
    identify the underlying file object. On Windows, ``os.DirEntry.stat()``
    populates its result from ``WIN32_FIND_DATA`` where ``st_ino`` is zero; when
    ``path`` is supplied, this function queries ``os.lstat(path)`` (and, if
    needed on Windows, ``GetFileInformationByHandle``) so hard links and file
    replacements are still detected reliably. Returns ``None`` only when the
    underlying filesystem genuinely provides no file identifier.
    """
    if stats.st_ino:
        return f"{stats.st_dev}:{stats.st_ino}"
    if path is None:
        return None
    try:
        full_stats = os.lstat(path)
    except OSError:
        full_stats = None
    if full_stats is not None and full_stats.st_ino:
        return f"{full_stats.st_dev}:{full_stats.st_ino}"
    if os.name == "nt":
        return _windows_handle_identity(path)
    return None


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("dwFileAttributes", ctypes.c_uint32),
        ("ftCreationTimeLow", ctypes.c_uint32),
        ("ftCreationTimeHigh", ctypes.c_uint32),
        ("ftLastAccessTimeLow", ctypes.c_uint32),
        ("ftLastAccessTimeHigh", ctypes.c_uint32),
        ("ftLastWriteTimeLow", ctypes.c_uint32),
        ("ftLastWriteTimeHigh", ctypes.c_uint32),
        ("dwVolumeSerialNumber", ctypes.c_uint32),
        ("nFileSizeHigh", ctypes.c_uint32),
        ("nFileSizeLow", ctypes.c_uint32),
        ("nNumberOfLinks", ctypes.c_uint32),
        ("nFileIndexHigh", ctypes.c_uint32),
        ("nFileIndexLow", ctypes.c_uint32),
    ]


def _windows_handle_identity(path: Path | str) -> str | None:
    """Read volume serial number and 64-bit file index from a Win32 file handle."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    kernel32 = windll.kernel32
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    flags = 0x02000000 | 0x00200000  # FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT

    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p

    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    get_info.restype = ctypes.c_int

    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int

    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(os.fspath(path), 0, share_all, None, open_existing, flags, None)
    if handle is None or handle == invalid_handle:
        return None
    try:
        info = _ByHandleFileInformation()
        if not get_info(handle, ctypes.byref(info)):
            return None
        file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
        if not file_index:
            return None
        return f"{int(info.dwVolumeSerialNumber)}:{file_index}"
    finally:
        close_handle(handle)
