"""Directory and identity guards for file mutations inside an approved tree."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from smart_file_organizer.utils.paths import app_data_dir, is_within, protected_root_reason


class BoundaryError(OSError):
    """The approved tree, a directory in it, or the target path is no longer safe."""

    def __init__(self, message: str, path: Path) -> None:
        super().__init__(errno.EINVAL, message, str(path))


def is_reparse_entry(stats: os.stat_result) -> bool:
    """Recognize symlinks and Windows reparse points, including junctions."""
    return stat.S_ISLNK(stats.st_mode) or bool(
        getattr(stats, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def storage_identity(stats: os.stat_result) -> str | None:
    return f"{stats.st_dev}:{stats.st_ino}" if stats.st_ino else None


def _plain_directory(stats: os.stat_result, path: Path) -> str:
    if is_reparse_entry(stats) or not stat.S_ISDIR(stats.st_mode):
        raise BoundaryError("Directory is blocked by a file or link", path)
    identity = storage_identity(stats)
    if identity is None:
        raise BoundaryError("Directory identity could not be verified", path)
    return identity


def directory_identity(path: Path) -> str:
    return _plain_directory(path.lstat(), path)


def writable_parts(path: Path, root: Path) -> tuple[str, ...]:
    """Check both lexical and resolved scope, including the private data folder."""
    if not path.is_absolute() or not root.is_absolute():
        raise BoundaryError("An absolute path inside the selected folder is required", path)
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise BoundaryError("Path is outside the selected folder", path) from exc
    if ".." in parts or not is_within(path, root):
        raise BoundaryError("Path is outside the selected folder", path)
    if is_within(path, app_data_dir()):
        raise BoundaryError("The application's data folder is protected", path)
    return parts


@dataclass(frozen=True)
class BoundDirectory:
    path: Path
    fd: int | None
    identity: str
    chain: tuple[tuple[Path, str], ...]

    def recheck(self) -> None:
        """Refuse a directory that was moved, replaced, or turned into a link."""
        for path, expected in self.chain:
            if directory_identity(path) != expected:
                raise BoundaryError("A directory changed; rebuild the preview before moving files", path)
        if is_within(self.path, app_data_dir()):
            raise BoundaryError("The application's data folder is protected", self.path)

    def stat_entry(self, name: str) -> os.stat_result:
        if self.fd is None:
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def mkdir(self, name: str) -> None:
        if self.fd is None:
            os.mkdir(self.path / name)
        else:
            os.mkdir(name, dir_fd=self.fd)

    def rmdir(self, name: str) -> None:
        if self.fd is None:
            os.rmdir(self.path / name)
        else:
            os.rmdir(name, dir_fd=self.fd)


@contextmanager
def bound_directory(
    path: Path, root: Path, *, root_identity: str | None = None, create: bool = False
) -> Iterator[BoundDirectory]:
    """Walk plain directories under the recorded root; pin them with descriptors on POSIX.

    Windows uses path-based OS operations and rechecks the entire directory chain at
    the mutation boundary. POSIX calls address directory entries relative to an open
    parent instead of resolving a potentially redirected path again.
    """
    parts = writable_parts(path, root)
    if protected := protected_root_reason(root):
        raise BoundaryError(f"The selected folder is protected: {protected}", root)
    root_now = directory_identity(root)
    if root_identity is not None and root_now != root_identity:
        raise BoundaryError("The selected folder changed; choose it and scan again", root)

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    use_fd = os.name == "posix" and bool(getattr(os, "O_DIRECTORY", 0) and getattr(os, "O_NOFOLLOW", 0))
    fd = os.open(root, flags) if use_fd else None
    try:
        if fd is not None and _plain_directory(os.fstat(fd), root) != root_now:
            raise BoundaryError("The selected folder changed; choose it and scan again", root)
        directory = BoundDirectory(root, fd, root_now, ((root, root_now),))
        for name in parts:
            child = directory.path / name
            try:
                stats = directory.stat_entry(name)
            except FileNotFoundError:
                if not create:
                    raise
                directory.recheck()
                with suppress(FileExistsError):
                    directory.mkdir(name)  # Validated immediately below, including the reparse flag.
                stats = directory.stat_entry(name)
            identity = _plain_directory(stats, child)
            if fd is not None:
                child_fd = os.open(name, flags, dir_fd=fd)
                os.close(fd)
                fd = child_fd
                if _plain_directory(os.fstat(fd), child) != identity:
                    raise BoundaryError("A directory changed during path validation", child)
            directory = BoundDirectory(child, fd, identity, (*directory.chain, (child, identity)))
        directory.recheck()
        yield directory
    finally:
        if fd is not None:
            os.close(fd)


def remove_recorded_directory(path: Path, root: Path, *, root_identity: str, directory_id: str | None) -> None:
    """Remove an empty app-created directory only if the recorded identity still matches."""
    if not directory_id or not writable_parts(path, root):
        raise BoundaryError("The app cannot verify this is a folder it created; left in place", path)
    with bound_directory(path.parent, root, root_identity=root_identity) as parent:
        parent.recheck()
        if _plain_directory(parent.stat_entry(path.name), path) != directory_id:
            raise BoundaryError("The folder was replaced; left in place", path)
        parent.rmdir(path.name)  # Atomic emptiness check; never recursive.
