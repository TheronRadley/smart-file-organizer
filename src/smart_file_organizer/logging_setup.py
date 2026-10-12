"""Structured logging: a rotating file in the data folder plus warnings on the console."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3


def configure_logging(log_path: Path, *, level: int = logging.INFO) -> None:
    """Send INFO and above to ``log_path`` (rotated) and WARNING and above to stderr."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("smart_file_organizer")
    root.setLevel(level)
    if getattr(root, "_sfo_configured", False):
        return
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(file_handler)
    root.addHandler(console)
    root._sfo_configured = True  # type: ignore[attr-defined]  # guards against duplicate handlers
