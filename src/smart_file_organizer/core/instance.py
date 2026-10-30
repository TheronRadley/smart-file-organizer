"""Single-instance coordination for startup recovery.

If two instances of the application share one data folder, the second one's
startup recovery would mark the first instance's in-progress operations as
interrupted while they are still running. A non-blocking OS file lock on a
lock file in the data folder identifies the first live instance: only the
instance that holds the lock runs startup recovery. Others still open the
application but skip recovery (and should treat history as shared).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

LOCK_FILE_NAME = "instance.lock"


def try_acquire_instance_lock(data_dir: Path) -> IO[bytes] | None:
    """Try to become the primary instance for ``data_dir``.

    Returns the open lock-file handle (keep it referenced for the process
    lifetime) or None when another live instance already holds the lock.
    """
    path = data_dir / LOCK_FILE_NAME
    try:
        handle = open(path, "a+b")  # noqa: SIM115 - intentionally kept open for process lifetime
    except OSError:
        logger.warning("Could not open instance lock file %s; treating this as the primary instance", path)
        return None
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.info("Another instance already holds the lock for %s; skipping startup recovery", data_dir)
        handle.close()
        return None
    return handle
