"""Guarded filesystem primitives shared by organization and undo.

Python's ``os.rename`` silently replaces an existing destination on POSIX
systems. :func:`move_no_overwrite` instead creates a second hard link first
(which fails if the destination exists) and only then removes the original.
Where hard links are unsupported (some FAT/exFAT or network volumes) it falls
back to a rename guarded by an existence check; that fallback has a small
race window, which is documented in ``docs/SAFETY.md``.
"""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def move_no_overwrite(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination`` without ever replacing an existing file.

    Raises ``FileExistsError`` if the destination is occupied. Other ``OSError``
    values are propagated; on failure the source is left in place.
    """
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError as exc:
        logger.info("Hard link unavailable for %s (%s); using guarded rename", source.name, exc.strerror)
        if os.path.lexists(destination):
            raise FileExistsError(errno.EEXIST, "Destination already exists", str(destination)) from exc
        os.rename(source, destination)
        return

    try:
        os.unlink(source)
    except OSError:
        # Roll back the extra name so the original stays where it was.
        os.unlink(destination)
        raise
