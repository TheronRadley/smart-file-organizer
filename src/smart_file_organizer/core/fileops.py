"""Guarded filesystem primitives shared by organization, undo and reporting.

Python's ``os.rename`` silently replaces an existing destination on POSIX
systems. :func:`move_no_overwrite` instead creates a second hard link first
(which fails atomically if the destination exists) and only then removes the
original.

Where hard links are unsupported:

* On Windows, ``os.rename`` is used because the Win32 rename call fails with
  :class:`FileExistsError` when the destination exists.
* On POSIX, ``os.rename`` would overwrite a destination created concurrently, so
  the move fails closed with :class:`OSError` and leaves both ``source`` and
  ``destination`` untouched (documented in ``docs/SAFETY.md``).

Rollback is identity-checked. Removing the source can fail *after* the link was
created; the extra name is then removed only when it can still be proved to be
the link this call created. A destination that another process replaced in the
meantime is left alone and reported, because deleting an unrelated file is
worse than leaving one extra link behind.

:func:`open_read_no_follow` is the read-side counterpart: it opens a file
without following a symbolic link and returns the ``stat`` of the handle that
was actually opened, so callers can compare it with the file they scanned.
"""

from __future__ import annotations

import errno
import logging
import os
import stat
from pathlib import Path
from typing import IO

from smart_file_organizer.core.scanner import file_identity

logger = logging.getLogger(__name__)

UNSAFE_POSIX_MOVE_MESSAGE = "Safe no-overwrite move is not supported on this filesystem (hard links unavailable)"

# ``O_NOFOLLOW`` is POSIX-only; on Windows links are followed, but the scanner
# never reports them, and the identity check below still applies. ``O_BINARY``
# is Windows-only and makes the read byte-exact (no newline translation).
_OPEN_READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)


class SourceChangedError(OSError):
    """The source path no longer refers to the file that was validated.

    Raised instead of following the path, so a link or a replacement file
    swapped in between the caller's check and the move cannot be moved.
    """

    def __init__(self, message: str, filename: str | None = None) -> None:
        super().__init__(errno.EINVAL, message, filename)


class MoveRollbackError(OSError):
    """A move failed and the extra link could not be removed safely.

    ``recovery`` tells the user what was left behind and how to resolve it.
    """

    def __init__(self, message: str, *, recovery: str, filename: str | None = None) -> None:
        super().__init__(errno.EIO, message, filename)
        self.recovery = recovery


def open_read_no_follow(path: Path | str) -> tuple[IO[bytes], os.stat_result]:
    """Open ``path`` for reading without following a symbolic link.

    Returns the open handle together with the ``stat`` of *that handle*, so the
    caller can verify it opened the file it scanned rather than whatever now
    answers to the name. Raises :class:`OSError` (``ELOOP`` for a symlink where
    ``O_NOFOLLOW`` is supported); callers that only report decide what to do.
    """
    fd = os.open(os.fspath(path), _OPEN_READ_FLAGS)
    try:
        return os.fdopen(fd, "rb"), os.fstat(fd)
    except OSError:
        os.close(fd)
        raise


def is_same_file(left: os.stat_result, right: os.stat_result) -> bool:
    """True when two ``stat`` results describe the same storage object.

    Returns False when the filesystem reports no inode number, because then
    identity cannot be proved and callers must assume the worst.
    """
    if not left.st_ino or not right.st_ino:
        return False
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def move_no_overwrite(source: Path, destination: Path, *, expected_identity: str | None = None) -> None:
    """Move ``source`` to ``destination`` without ever replacing an existing file.

    Raises ``FileExistsError`` if the destination is occupied. Other ``OSError``
    values are propagated; on failure the source is left in place. When
    ``expected_identity`` is given (the ``file_id`` recorded at scan time), the
    source is re-checked immediately before the link is created and the move is
    refused if the path now points at a different file.
    """
    source_stats = os.lstat(source)
    if not stat.S_ISREG(source_stats.st_mode):
        # Refuse to follow a link that was swapped in after the caller checked.
        raise SourceChangedError("Source is not a regular file; refusing to follow a link", str(source))
    if expected_identity is not None and file_identity(source_stats, source) != expected_identity:
        raise SourceChangedError("Source was replaced before the move; refusing to move it", str(source))

    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError as exc:
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, "Destination already exists", str(destination)) from exc
        if isinstance(exc, FileNotFoundError) or not os.path.lexists(source):
            raise
        if os.name == "nt":
            logger.info(
                "Hard link unavailable for %s (%s); using Windows no-overwrite rename",
                source.name,
                exc.strerror,
            )
            os.rename(source, destination)
            return
        logger.warning(
            "Refusing to move %s without hard-link support on POSIX (%s); "
            "destination overwrite cannot be prevented safely",
            source.name,
            exc.strerror or exc,
        )
        raise OSError(errno.ENOTSUP, UNSAFE_POSIX_MOVE_MESSAGE, str(source)) from exc

    # Remember exactly what this call created, so the rollback can recognise it.
    try:
        linked_stats: os.stat_result | None = os.lstat(destination)
    except OSError:
        linked_stats = None

    if linked_stats is not None and not _matches_validated(linked_stats, source_stats):
        # The source was replaced between our check and the link: the link we
        # just created names the replacement, not the validated file. Remove
        # only that link (never the source path) and refuse the move.
        _remove_created_link(source, destination, linked_stats)
        raise SourceChangedError("Source was replaced before the move; refusing to move it", str(source))

    # Final identity check immediately before removing the source name: if the
    # source path was replaced after linking, unlinking it would delete an
    # unrelated file. In that case remove only the link this call created.
    try:
        current_source_stats: os.stat_result | None = os.lstat(source)
    except OSError:
        current_source_stats = None
    if current_source_stats is None or not _matches_validated(current_source_stats, source_stats):
        # Do NOT remove the destination link here: the replacement removed the
        # original source name, so the destination may be the only remaining
        # name of the validated file. Keep it and report both paths.
        _report_unverifiable(
            source,
            destination,
            SourceChangedError("Source was replaced before the move"),
            "the source path now holds a different file",
        )

    try:
        os.unlink(source)
    except OSError as exc:
        _roll_back(source, destination, linked_stats, source_stats, exc)
        raise


def _matches_validated(candidate: os.stat_result, validated: os.stat_result) -> bool:
    """True when ``candidate`` still describes the file that was validated.

    Identity (device + inode) alone is not enough: an inode can be reused by a
    replacement created immediately after the original was removed. Size and
    modification time are therefore compared as well. A replacement that
    deliberately reproduces all four values is indistinguishable by metadata
    and remains a documented limitation.
    """
    return (
        is_same_file(candidate, validated)
        and candidate.st_size == validated.st_size
        and candidate.st_mtime_ns == validated.st_mtime_ns
    )


def _remove_created_link(source: Path, destination: Path, linked_stats: os.stat_result) -> None:
    """Remove the destination link this call created, verified by identity.

    Used when the source turned out to have been replaced: the destination is
    removed only while it still provably is the link created here. If that
    cannot be proved or the removal fails, :class:`MoveRollbackError` reports
    both paths instead of deleting anything uncertain.
    """
    try:
        current = os.lstat(destination)
    except OSError:
        return  # Already gone; nothing extra was left behind.
    if not is_same_file(current, linked_stats):
        _report_unverifiable(
            source, destination, OSError("Destination changed"), "that path no longer holds the link this move created"
        )
        return
    try:
        os.unlink(destination)
    except OSError as exc:
        _report_unverifiable(source, destination, exc, "the extra link could not be removed")


def _roll_back(
    source: Path,
    destination: Path,
    linked_stats: os.stat_result | None,
    source_stats: os.stat_result,
    cause: OSError,
) -> None:
    """Remove the link created by :func:`move_no_overwrite`, or explain why not.

    The destination is removed only while it is still provably the hard link
    this call created *and* still refers to the same file object as the source.
    Anything else is left in place: an extra link is recoverable, a deleted
    file that another program just wrote is not.
    """
    if linked_stats is None:
        _report_unverifiable(source, destination, cause, "its identity could not be read after linking")
        return
    try:
        current = os.lstat(destination)
    except OSError:
        return  # The link is already gone; nothing to clean up.
    if not is_same_file(current, linked_stats) or not is_same_file(current, source_stats):
        _report_unverifiable(source, destination, cause, "that path no longer holds the link this move created")
        return
    try:
        os.unlink(destination)
    except OSError as exc:
        # A failed cleanup must never be silent: an extra filesystem entry is
        # being left behind, and the user needs both paths to resolve it.
        _report_unverifiable(source, destination, exc, "the extra link could not be removed")


def _report_unverifiable(source: Path, destination: Path, cause: OSError, why: str) -> None:
    recovery = (
        f"'{source}' was left in place and an extra copy of it remains at '{destination}' "
        f"because {why}. The copy was kept rather than deleted. "
        f"Check both paths and remove the one you do not need."
    )
    logger.error("Rollback of %s kept '%s': %s", source.name, destination, why)
    raise MoveRollbackError(
        f"Move failed ({cause.strerror or cause}) and the extra copy at '{destination}' was kept, not deleted",
        recovery=recovery,
        filename=str(destination),
    ) from cause
