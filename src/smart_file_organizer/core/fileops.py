"""Guarded filesystem primitives shared by organization, undo and reporting.

A move uses one native no-replace operation. Emulating it by linking and then
unlinking is not safe: the two names can change independently, and a cleanup
cannot prove it is not deleting an unrelated file or the last surviving name.
On unsupported filesystems, a move therefore fails before changing either file.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from functools import lru_cache
from pathlib import Path
from typing import IO

from smart_file_organizer.core.boundary import BoundDirectory, bound_directory, is_reparse_entry, writable_parts
from smart_file_organizer.core.cancellation import Cancellation
from smart_file_organizer.core.scanner import file_identity

UNSUPPORTED_MOVE = "Safe atomic no-overwrite move is not supported on this filesystem; neither file was changed"
_UNSUPPORTED_RENAME = frozenset({errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})

# O_NOFOLLOW guards the final component where supported. O_NONBLOCK prevents
# a replacement FIFO from blocking before the caller can check the handle.
_OPEN_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
)


class SourceChangedError(OSError):
    """The source no longer matches the file that was approved."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(errno.EINVAL, message, str(path))


class MoveCancelled(OSError):
    """Cancellation was observed before the native rename started."""

    def __init__(self, path: Path) -> None:
        super().__init__(errno.ECANCELED, "Cancelled before the file was moved", str(path))


def open_read_no_follow(path: Path | str) -> tuple[IO[bytes], os.stat_result]:
    """Open a read handle and return metadata for the object actually opened."""
    if is_reparse_entry(os.lstat(path)):
        raise OSError(errno.ELOOP, "Refusing to open a link or reparse point", os.fspath(path))
    fd = os.open(os.fspath(path), _OPEN_READ_FLAGS)
    try:
        stats = os.fstat(fd)
        return os.fdopen(fd, "rb"), stats
    except BaseException:
        os.close(fd)
        raise


def move_no_overwrite(
    source: Path,
    destination: Path,
    *,
    expected_identity: str | None = None,
    expected_size: int | None = None,
    expected_modified_ns: int | None = None,
    root: Path | None = None,
    root_identity: str | None = None,
    source_parent_identity: str | None = None,
    destination_parent_identity: str | None = None,
    cancel_event: Cancellation | None = None,
) -> None:
    """Move one validated file, without ever clobbering the destination.

    Organization and Undo supply their approved root and full file signature.
    The native operation is a single same-filesystem rename, not copy/delete.
    """
    source, destination = source.absolute(), destination.absolute()
    if root is None:
        try:
            root = Path(os.path.commonpath((source.parent, destination.parent)))
        except ValueError as exc:
            raise OSError(errno.EXDEV, "Source and destination are on different drives", str(source)) from exc
    writable_parts(source, root)
    writable_parts(destination, root)
    with (
        bound_directory(source.parent, root, root_identity=root_identity) as source_dir,
        bound_directory(destination.parent, root, root_identity=root_identity) as destination_dir,
    ):
        destination_dir.recheck()
        source_dir.recheck()
        if source_parent_identity is not None and source_dir.identity != source_parent_identity:
            raise SourceChangedError("The original parent folder changed during the move", source)
        if destination_parent_identity is not None and destination_dir.identity != destination_parent_identity:
            raise SourceChangedError("The destination folder changed during the move", destination)
        stats = source_dir.stat_entry(source.name)
        if not stat.S_ISREG(stats.st_mode) or is_reparse_entry(stats):
            raise SourceChangedError("Source is not a regular file; refusing to follow a link", source)
        if expected_identity is not None:
            identity = file_identity(stats, source if os.name == "nt" else None)
            if identity != expected_identity:
                raise SourceChangedError("Source is no longer the approved file", source)
        if (expected_size is not None and stats.st_size != expected_size) or (
            expected_modified_ns is not None and stats.st_mtime_ns != expected_modified_ns
        ):
            raise SourceChangedError("Source size or modification time changed after approval", source)
        if source_dir.chain[0] != destination_dir.chain[0]:
            raise SourceChangedError("The selected folder changed during the move", source)
        if cancel_event is not None and cancel_event.is_set():
            raise MoveCancelled(source)
        _rename_no_replace(source_dir, source.name, destination_dir, destination.name)


@lru_cache(maxsize=1)
def _posix_rename() -> tuple[ctypes._CFuncPtr, int, int] | None:
    if sys.platform.startswith("linux"):
        symbol, flag, cwd = "renameat2", 1, -100  # RENAME_NOREPLACE, AT_FDCWD
    elif sys.platform == "darwin":
        symbol, flag, cwd = "renameatx_np", 0x00000004, -2  # RENAME_EXCL, AT_FDCWD
    else:
        return None
    try:
        call = getattr(ctypes.CDLL(None, use_errno=True), symbol)
    except (OSError, AttributeError):
        return None
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    call.restype = ctypes.c_int
    return call, flag, cwd


def _rename_no_replace(source: BoundDirectory, name: str, destination: BoundDirectory, target: str) -> None:
    if os.name != "nt" and (source.fd is None or destination.fd is None):
        raise OSError(errno.ENOTSUP, UNSUPPORTED_MOVE, str(source.path / name))
    rename_no_replace(source.path / name, destination.path / target, src_fd=source.fd, dst_fd=destination.fd)


def rename_no_replace(source: Path, destination: Path, *, src_fd: int | None = None, dst_fd: int | None = None) -> None:
    """Publish a name atomically; callers are responsible for checking path scope and identity."""
    if os.name == "nt":
        # CPython uses MoveFileExW without REPLACE_EXISTING or COPY_ALLOWED.
        os.rename(source, destination)
        return
    native = _posix_rename()
    if native is None:
        raise OSError(errno.ENOTSUP, UNSUPPORTED_MOVE, str(source))
    rename, flag, cwd = native
    source_arg = source if src_fd is None else source.name
    target_arg = destination if dst_fd is None else destination.name
    if (
        rename(
            cwd if src_fd is None else src_fd,
            os.fsencode(source_arg),
            cwd if dst_fd is None else dst_fd,
            os.fsencode(target_arg),
            flag,
        )
        == 0
    ):
        return
    error = ctypes.get_errno()
    if error in _UNSUPPORTED_RENAME:
        raise OSError(errno.ENOTSUP, UNSUPPORTED_MOVE, str(source))
    raise OSError(error, os.strerror(error), str(source), None, str(destination))
