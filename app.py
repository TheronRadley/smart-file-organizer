"""Convenience launcher: ``python app.py`` runs the desktop application from a source checkout."""

from __future__ import annotations

import sys
from pathlib import Path

SOURCE_DIR = Path(__file__).resolve().parent / "src"
if SOURCE_DIR.is_dir() and str(SOURCE_DIR) not in sys.path:
    sys.path.insert(0, str(SOURCE_DIR))

from smart_file_organizer.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
