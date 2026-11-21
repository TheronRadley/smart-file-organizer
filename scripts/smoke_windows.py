"""Launch the built executable and verify the packaged desktop workflow."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "dist" / "SmartFileOrganizer" / "SmartFileOrganizer.exe"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", nargs="?", default=DEFAULT_EXE, type=Path)
    exe = parser.parse_args().executable.resolve()
    if not exe.is_file():
        parser.error(f"Executable not found: {exe}. Build it with scripts/build_windows.py first.")
    with TemporaryDirectory(prefix="sfo-packaged-smoke-") as scratch:
        data_dir = Path(scratch) / "profile"
        try:
            subprocess.run(
                [str(exe), "--smoke-test"],
                cwd=scratch,
                env={**os.environ, "SFO_DATA_DIR": str(data_dir)},
                timeout=90,
                check=True,
            )
            if (data_dir / "smoke-ok.txt").read_text(encoding="utf-8").strip() != "passed":
                raise RuntimeError("The executable did not report a successful round trip")
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            log = data_dir / "app.log"
            if log.is_file():
                print(log.read_text(encoding="utf-8", errors="replace"))
            raise SystemExit(f"Packaged smoke test failed: {exc}") from exc
    print("Packaged startup, navigation, preview, apply, and undo passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
