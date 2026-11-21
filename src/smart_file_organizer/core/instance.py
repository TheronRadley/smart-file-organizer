"""Hold an exclusive OS lock for the lifetime of a writable app instance."""

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
        logger.warning("Could not open instance lock file %s; startup refused", path)
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
        logger.info("Could not acquire the exclusive instance lock for %s; startup refused", data_dir)
        handle.close()
        return None
    return handle
