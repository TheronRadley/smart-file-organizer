"""Coordinated, per-folder locking for operations that move files.

Manual organization, guarded undo, and watcher-applied batches must never
modify the same folder at the same time: two batches racing on one tree could
act on each other's half-moved state. This module provides one non-blocking
lock per normalized root path, shared by every entry point that moves files.
Operations on *different* folders still run in parallel.

The lock is advisory and process-local: it coordinates this application's own
operations, not other programs (those are handled by identity re-validation at
the move boundary).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from smart_file_organizer.utils.paths import normalise

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(root: Path | str) -> threading.Lock:
    key = normalise(root)
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


@contextmanager
def operation_lock(root: Path | str) -> Iterator[bool]:
    """Try to hold the move lock for ``root``; yields False when already held.

    Non-blocking by design: a second operation on the same folder is reported
    to the user (or skipped) instead of queueing up behind a long batch and
    then acting on stale previews.
    """
    lock = _lock_for(root)
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()
