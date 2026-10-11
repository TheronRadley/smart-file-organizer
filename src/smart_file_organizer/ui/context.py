"""Application state shared by the window and every page."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from smart_file_organizer.config import Settings, save_settings
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.duplicates import DuplicateReport
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.large_files import LargeFileReport
from smart_file_organizer.core.models import OrganizePlan, ScanResult
from smart_file_organizer.core.watcher import WatchManager
from smart_file_organizer.database.db import Database


@dataclass
class AppContext:
    """Everything pages need. Results are in-memory "current" data; history is persistent."""

    settings: Settings
    settings_path: Path
    data_dir: Path
    database: Database
    history: HistoryRepository
    log_path: Path
    watches: WatchManager = field(default_factory=WatchManager)
    last_scan: ScanResult | None = None
    last_plan: OrganizePlan | None = None
    last_duplicates: DuplicateReport | None = None
    last_large: LargeFileReport | None = None
    recovered_database: Path | None = None

    @property
    def classifier(self) -> Classifier:
        return Classifier.from_settings(self.settings)

    def save_settings(self) -> None:
        save_settings(self.settings, self.settings_path)
