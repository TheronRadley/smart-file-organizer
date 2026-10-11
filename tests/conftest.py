"""Shared fixtures. Every test works in its own temporary directory.

The application data directory is redirected for each test, so the suite never
reads or writes a real user profile or a real user folder.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from smart_file_organizer.config import (
    DEFAULT_EXCLUDED_FOLDERS,
    DEFAULT_EXCLUDED_PATTERNS,
)
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.database.db import Database
from smart_file_organizer.utils.paths import DATA_DIR_ENV_VAR

FIXED_MTIME = 1_700_000_000  # deterministic timestamps for synthetic files


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "appdata"
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(data_dir))
    return data_dir


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Database]:
    db = Database(tmp_path / "history-test.sqlite3")
    yield db
    db.close()


@pytest.fixture
def history(database: Database) -> HistoryRepository:
    return HistoryRepository(database)


@pytest.fixture
def classifier() -> Classifier:
    return Classifier()


@pytest.fixture
def options() -> ScanOptions:
    return ScanOptions(
        recursive=False,
        excluded_folders=DEFAULT_EXCLUDED_FOLDERS,
        excluded_patterns=DEFAULT_EXCLUDED_PATTERNS,
    )


@pytest.fixture
def recursive_options(options: ScanOptions) -> ScanOptions:
    return ScanOptions(
        recursive=True,
        excluded_folders=options.excluded_folders,
        excluded_patterns=options.excluded_patterns,
    )


def make_file(path: Path, content: bytes = b"data", mtime: int | None = FIXED_MTIME) -> Path:
    """Create a synthetic file (and parent folders) with deterministic content and timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def symlinks_supported(directory: Path) -> bool:
    """Probe whether this environment allows creating symbolic links."""
    probe_target = directory / "_probe_target"
    probe_link = directory / "_probe_link"
    probe_target.write_text("x")
    try:
        os.symlink(probe_target, probe_link)
    except (OSError, NotImplementedError):
        return False
    finally:
        for candidate in (probe_link, probe_target):
            if candidate.is_symlink() or candidate.exists():
                candidate.unlink()
    return True


def is_root_user() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0
