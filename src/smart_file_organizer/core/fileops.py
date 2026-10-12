"""Guarded filesystem primitives shared by organization and undo.

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
"""

from __future__ import annotations

import errno
import logging
import os
from contextlib import suppress
from pathlib import Path

logger = logging.getLogger(__name__)

UNSAFE_POSIX_MOVE_MESSAGE = "Safe no-overwrite move is not supported on this filesystem (hard links unavailable)"


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

    try:
        os.unlink(source)
    except OSError:
        # Roll back the extra name so the original stays where it was.
        with suppress(OSError):
            os.unlink(destination)
        raise
