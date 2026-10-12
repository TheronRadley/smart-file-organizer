"""Path helpers used by every operation that touches user files.

The helpers here are deliberately conservative: they normalise paths, compare
them against a trusted root (following symbolic links and Windows junctions),
and refuse names that could escape or confuse the filesystem.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP_DIR_NAME = "SmartFileOrganizer"
DATA_DIR_ENV_VAR = "SFO_DATA_DIR"
MAX_UNIQUE_ATTEMPTS = 10_000
MAX_NAME_LENGTH = 120

_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
)


class UnsafePathError(ValueError):
    """Raised when a path would escape its trusted root or is otherwise unsafe."""


def app_data_dir() -> Path:
    """Return the per-user directory used for settings, the database and logs.

    ``SFO_DATA_DIR`` overrides the location (used by the test-suite and portable installs).
    """
    override = os.environ.get(DATA_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def normalise(path: Path | str) -> str:
    """Return a case-normalised, symlink-resolved absolute string for comparisons."""
    return os.path.normcase(os.path.realpath(os.fspath(path)))


def is_within(path: Path | str, root: Path | str) -> bool:
    """Return True when ``path`` is ``root`` or lies inside it (after resolving links)."""
    candidate = normalise(path)
    base = normalise(root)
    if candidate == base:
        return True
    return candidate.startswith(base.rstrip(os.sep) + os.sep)


def ensure_within(path: Path | str, root: Path | str) -> Path:
    """Return ``path`` as a Path, or raise :class:`UnsafePathError` if it escapes ``root``."""
    if not is_within(path, root):
        raise UnsafePathError(f"Path is outside the selected folder: {path}")
    return Path(path)


def is_link_like(path: Path | str) -> bool:
    """True for symbolic links and (on Windows) junctions."""
    if os.path.islink(path):
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def validate_name(name: str) -> str:
    """Return ``name`` if it is a safe single path component, else raise UnsafePathError."""
    cleaned = name
    if not cleaned or cleaned.strip() != cleaned or cleaned in {".", ".."}:
        raise UnsafePathError("A folder name cannot be empty, '.' or '..', or start/end with spaces.")
    if _INVALID_NAME_CHARS.search(cleaned):
        raise UnsafePathError(f"'{cleaned}' contains characters that are not allowed in folder names.")
    if cleaned != cleaned.rstrip(". "):
        raise UnsafePathError(f"'{cleaned}' cannot end with a dot or a space.")
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        raise UnsafePathError(f"'{cleaned}' is a reserved name on Windows.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise UnsafePathError("Folder names must be at most 120 characters.")
    return cleaned


def sanitize_name(name: str, fallback: str = "Other") -> str:
    """Turn arbitrary text (such as a file extension) into a safe folder name."""
    cleaned = _INVALID_NAME_CHARS.sub("_", name.strip()).rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    if cleaned.split(".")[0].upper() in _RESERVED_NAMES:
        cleaned = "_" + cleaned
    return cleaned[:MAX_NAME_LENGTH]


def unique_path(target: Path) -> Path:
    """Return ``target`` if free, otherwise ``'name (2).ext'``, ``'name (3).ext'``, and so on."""
    if not os.path.lexists(target):
        return target
    stem, suffix = target.stem, target.suffix
    for number in range(2, MAX_UNIQUE_ATTEMPTS):
        candidate = target.with_name(f"{stem} ({number}){suffix}")
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(f"No free name found for {target}")
