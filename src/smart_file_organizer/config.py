"""Configuration: default category rules, user settings and their persistence.

Category rules are defined centrally in :data:`DEFAULT_CATEGORIES`. Users can
rename destination folders, reassign individual extensions to another category
and disable categories; those choices are stored in ``settings.json``.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from smart_file_organizer.utils.paths import UnsafePathError, validate_name

logger = logging.getLogger(__name__)

SETTINGS_FILE_NAME = "settings.json"
DATABASE_FILE_NAME = "history.sqlite3"
LOG_FILE_NAME = "app.log"

ORGANIZE_BY_TYPE = "type"
ORGANIZE_BY_EXTENSION = "extension"
ORGANIZE_BY_DATE = "date"
ORGANIZE_MODES = (ORGANIZE_BY_TYPE, ORGANIZE_BY_EXTENSION, ORGANIZE_BY_DATE)

COLLISION_RENAME = "rename"
COLLISION_SKIP = "skip"
COLLISION_STRATEGIES = (COLLISION_RENAME, COLLISION_SKIP)

THEMES = ("dark", "light")


@dataclass(frozen=True)
class CategoryRule:
    """A named category, its default destination folder and the extensions it covers."""

    name: str
    folder: str
    extensions: tuple[str, ...]


DEFAULT_CATEGORIES: tuple[CategoryRule, ...] = (
    CategoryRule("Documents", "Documents", ("pdf", "docx", "doc", "txt", "rtf", "odt")),
    CategoryRule("Images", "Images", ("png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tiff")),
    CategoryRule("Videos", "Videos", ("mp4", "mkv", "mov", "avi", "webm")),
    CategoryRule("Audio", "Audio", ("mp3", "wav", "flac", "aac", "ogg", "m4a")),
    CategoryRule("Archives", "Archives", ("zip", "rar", "7z", "tar", "gz")),
    CategoryRule(
        "Code",
        "Code",
        ("py", "js", "ts", "html", "css", "json", "xml", "java", "cpp", "c", "go", "rs"),
    ),
    CategoryRule("Spreadsheets", "Spreadsheets", ("xlsx", "xls", "csv", "ods")),
    CategoryRule("Presentations", "Presentations", ("pptx", "ppt", "key")),
    CategoryRule("Executables", "Executables", ("exe", "msi", "apk", "dmg")),
    CategoryRule("Fonts", "Fonts", ("ttf", "otf", "woff", "woff2")),
)

OTHER_CATEGORY = CategoryRule("Other", "Other", ())

DEFAULT_EXCLUDED_FOLDERS: tuple[str, ...] = (".git", "node_modules", "__pycache__", ".venv", "venv")
DEFAULT_EXCLUDED_PATTERNS: tuple[str, ...] = (
    ".*",  # hidden files on POSIX systems; rarely user documents and risky to move
    "~$*",  # Office lock files
    "*.tmp",
    "*.part",
    "*.crdownload",  # browser partial downloads
    "*.download",
    "desktop.ini",
    "Thumbs.db",
)


@dataclass
class Settings:
    """User-editable settings persisted as JSON."""

    collision_strategy: str = COLLISION_RENAME
    organize_by: str = ORGANIZE_BY_TYPE
    recursive_scan: bool = False
    excluded_folders: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_FOLDERS))
    excluded_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDED_PATTERNS))
    category_folders: dict[str, str] = field(default_factory=dict)
    extension_overrides: dict[str, str] = field(default_factory=dict)
    disabled_categories: list[str] = field(default_factory=list)
    large_file_min_mb: float = 100.0
    watch_folders: list[str] = field(default_factory=list)
    watch_auto_apply: bool = False
    watch_recursive: bool = False
    watch_interval_seconds: int = 5
    theme: str = "dark"


def settings_from_dict(raw: dict[str, Any]) -> Settings:
    """Build settings from parsed JSON, ignoring unknown keys and invalid values."""
    settings = Settings()
    defaults = asdict(settings)
    for item in fields(Settings):
        if item.name not in raw:
            continue
        value = raw[item.name]
        default = defaults[item.name]
        expected = type(default)
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or isinstance(value, bool) != isinstance(default, bool):
            logger.warning("Ignoring invalid setting %s=%r", item.name, value)
            continue
        setattr(settings, item.name, value)

    if settings.collision_strategy not in COLLISION_STRATEGIES:
        settings.collision_strategy = COLLISION_RENAME
    if settings.organize_by not in ORGANIZE_MODES:
        settings.organize_by = ORGANIZE_BY_TYPE
    if settings.theme not in THEMES:
        settings.theme = "dark"
    settings.watch_interval_seconds = max(1, min(settings.watch_interval_seconds, 3600))
    settings.large_file_min_mb = max(0.0, settings.large_file_min_mb)
    return settings


def load_settings(path: Path) -> Settings:
    """Load settings from ``path``; fall back to defaults if missing or unreadable."""
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.exception("Settings file %s is unreadable; using defaults", path)
        return Settings()
    if not isinstance(raw, dict):
        logger.warning("Settings file %s has an unexpected structure; using defaults", path)
        return Settings()
    return settings_from_dict(raw)


def save_settings(settings: Settings, path: Path) -> None:
    """Write settings atomically (temporary file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(settings), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def folder_name_for(category: str, default: str, overrides: dict[str, str]) -> str:
    """Return the validated destination folder name for ``category``."""
    custom = overrides.get(category)
    if not custom:
        return default
    try:
        return validate_name(custom)
    except UnsafePathError:
        logger.warning("Invalid folder name %r for category %s; using %s", custom, category, default)
        return default
