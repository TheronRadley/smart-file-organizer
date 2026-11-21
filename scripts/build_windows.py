"""Build the Windows distribution using the same entry point as CI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("The Windows executable must be built on Windows.")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "SmartFileOrganizer",
        "--paths",
        "src",
        "--collect-all",
        "customtkinter",
        "app.py",
    ]
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
