"""Operating-system integration: revealing folders in the native file manager."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def open_in_file_manager(path: Path) -> None:
    """Open a folder, or select a file, in the platform's file manager.

    Commands are passed as argument lists (never through a shell) so file names
    cannot inject commands.
    """
    if not path.exists():
        raise FileNotFoundError(f"'{path}' no longer exists.")
    if os.name == "nt":
        if path.is_file():
            subprocess.Popen(["explorer", f"/select,{path}"])
        else:
            os.startfile(path)  # type: ignore[attr-defined]  # Windows only
        return
    if sys.platform == "darwin":
        target = ["open", "-R", str(path)] if path.is_file() else ["open", str(path)]
        subprocess.Popen(target)
        return
    folder = path.parent if path.is_file() else path
    subprocess.Popen(["xdg-open", str(folder)])
    logger.debug("Opened %s", folder)
