"""Optional folder monitoring (automation).

Design choices:

* **Polling instead of OS events.** A periodic scan of the monitored folder
  uses only the standard library and behaves the same on Windows, macOS and
  Linux. The interval is configurable.
* **Stability before action.** A file is only handled once its size and
  modification time have not changed for ``stable_seconds`` and it has not been
  modified recently, so partially copied files are left alone.
* **No repeat processing.** Every handled file is remembered by
  (path, size, modification time). Files are only handled again if they change.
* **Preview first.** Unless ``auto_apply`` is enabled, stable files are listed as
  pending and nothing is moved until the user applies them.
* **Same safety rules.** Monitoring uses the organizer, so exclusions, collision
  handling and revalidation apply exactly as in the Organize page.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_WATCH,
    BatchOutcome,
    OrganizePlan,
    PlannedOperation,
    ScanOptions,
)
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import ScanError, scan_folder

logger = logging.getLogger(__name__)

WATCHER_STOPPED = "stopped"
WATCHER_RUNNING = "running"
WATCHER_PAUSED = "paused"
MAX_EVENTS = 200

FileKey = tuple[str, int, int]
Clock = Callable[[], float]


@dataclass(frozen=True)
class WatchConfig:
    organize_by: str
    collision_strategy: str
    recursive: bool
    auto_apply: bool
    scan_options: ScanOptions
    disabled_categories: frozenset[str] = frozenset()
    interval_seconds: float = 5.0
    stable_seconds: float = 10.0


@dataclass(frozen=True)
class PendingMove:
    source: Path
    destination: Path | None
    size: int


@dataclass(frozen=True)
class WatchEvent:
    timestamp: float
    message: str

    @property
    def text(self) -> str:
        return f"{datetime.fromtimestamp(self.timestamp):%H:%M:%S}  {self.message}"


@dataclass
class _WatchState:
    observed: dict[FileKey, float] = field(default_factory=dict)
    handled: set[FileKey] = field(default_factory=set)
    pending: list[PendingMove] = field(default_factory=list)
    pending_keys: set[FileKey] = field(default_factory=set)


def _key(operation: PlannedOperation) -> FileKey:
    return (str(operation.source), operation.size, operation.modified_ns)


class FolderWatcher:
    """Monitors one folder. Thread-safe for reads of status, pending moves and events."""

    def __init__(
        self,
        folder: Path,
        config: WatchConfig,
        *,
        classifier: Classifier,
        history: HistoryRepository,
        clock: Clock = time.time,
    ) -> None:
        self.folder = folder
        self.config = config
        self._classifier = classifier
        self._history = history
        self._clock = clock
        self._state = _WatchState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._thread: threading.Thread | None = None
        self._status = WATCHER_STOPPED
        self._events: deque[WatchEvent] = deque(maxlen=MAX_EVENTS)
        self.last_error: str | None = None

    # ----- status and events --------------------------------------------

    @property
    def status(self) -> str:
        return self._status

    @property
    def pending(self) -> list[PendingMove]:
        with self._lock:
            return list(self._state.pending)

    @property
    def events(self) -> list[WatchEvent]:
        with self._lock:
            return list(self._events)

    def _log(self, message: str) -> None:
        with self._lock:
            self._events.append(WatchEvent(self._clock(), message))
        logger.info("[watch %s] %s", self.folder.name, message)

    # ----- polling ------------------------------------------------------

    def poll_once(self) -> list[BatchOutcome]:
        """Scan once, update pending moves and, if auto-apply is on, act on stable files.

        Returns the batch outcomes produced by this poll (empty unless auto-apply moved files).
        """
        now = self._clock()
        if not self.folder.is_dir():
            self.last_error = "The monitored folder no longer exists."
            self._log(self.last_error)
            return []

        try:
            scan = scan_folder(self.folder, self.config.scan_options)
        except ScanError as exc:
            self.last_error = str(exc)
            self._log(self.last_error)
            return []

        plan = build_plan(
            scan,
            self._classifier,
            organize_by=self.config.organize_by,
            collision_strategy=self.config.collision_strategy,
            disabled_categories=self.config.disabled_categories,
        )
        stable = self._stable_operations(plan, now)

        with self._lock:
            self._state.pending = [PendingMove(op.source, op.destination, op.size) for op in stable]
            self._state.pending_keys = {_key(op) for op in stable}

        if not self.config.auto_apply or not stable:
            return []
        return [self._apply(plan, stable)]

    def apply_pending(self) -> BatchOutcome | None:
        """Apply the moves that were listed as pending, if they are still unchanged."""
        with self._lock:
            approved = set(self._state.pending_keys)
        if not approved:
            return None
        scan = scan_folder(self.folder, self.config.scan_options)
        plan = build_plan(
            scan,
            self._classifier,
            organize_by=self.config.organize_by,
            collision_strategy=self.config.collision_strategy,
            disabled_categories=self.config.disabled_categories,
        )
        operations = [op for op in plan.ready if _key(op) in approved]
        if not operations:
            self._log("Pending files changed or disappeared; nothing was moved.")
            return None
        return self._apply(plan, operations)

    def _stable_operations(self, plan: OrganizePlan, now: float) -> list[PlannedOperation]:
        stable: list[PlannedOperation] = []
        live_keys: set[FileKey] = set()
        with self._lock:
            for operation in plan.ready:
                key = _key(operation)
                live_keys.add(key)
                if key in self._state.handled:
                    continue
                first_seen = self._state.observed.setdefault(key, now)
                settled = now - first_seen >= self.config.stable_seconds
                quiet = now - operation.modified_ns / 1_000_000_000 >= self.config.stable_seconds
                if settled and quiet:
                    stable.append(operation)
            for key in list(self._state.observed):
                if key not in live_keys:
                    del self._state.observed[key]
        return stable

    def _apply(self, plan: OrganizePlan, operations: list[PlannedOperation]) -> BatchOutcome:
        outcome = execute_plan(
            plan,
            history=self._history,
            selected=[op.index for op in operations],
            kind=BATCH_WATCH,
        )
        with self._lock:
            for operation in operations:
                # Handled whether or not the move succeeded, so failures are not retried every poll.
                self._state.handled.add(_key(operation))
                self._state.observed.pop(_key(operation), None)
            self._state.pending = []
            self._state.pending_keys = set()
        moved = outcome.success_count
        self._log(f"Organized {moved} file(s), skipped {outcome.skipped_count}, failed {outcome.failed_count}.")
        return outcome

    # ----- thread control ------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._resume_event.set()
        self._status = WATCHER_RUNNING
        self._log(
            "Monitoring started (auto-apply on)." if self.config.auto_apply else "Monitoring started (preview only)."
        )
        self._thread = threading.Thread(target=self._run, name=f"watch-{self.folder.name}", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        if self._status == WATCHER_RUNNING:
            self._resume_event.clear()
            self._status = WATCHER_PAUSED
            self._log("Paused.")

    def resume(self) -> None:
        if self._status == WATCHER_PAUSED:
            self._resume_event.set()
            self._status = WATCHER_RUNNING
            self._log("Resumed.")

    def stop(self, timeout: float = 10.0) -> None:
        self._stop_event.set()
        self._resume_event.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        if self._status != WATCHER_STOPPED:
            self._status = WATCHER_STOPPED
            self._log("Monitoring stopped.")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._resume_event.wait()
            if self._stop_event.is_set():
                break
            try:
                self.poll_once()
                self.last_error = None
            except (OSError, sqlite3.Error) as exc:
                # Logged with its traceback and surfaced in the UI; the watcher keeps running.
                logger.exception("Monitoring poll failed for %s", self.folder)
                self.last_error = f"Monitoring hit an error and will retry: {exc.__class__.__name__}"
                self._log(self.last_error)
            self._stop_event.wait(self.config.interval_seconds)


class WatchManager:
    """Owns the set of monitored folders for the application."""

    def __init__(self) -> None:
        self._watchers: dict[str, FolderWatcher] = {}

    def add(self, watcher: FolderWatcher) -> None:
        key = str(watcher.folder)
        if key in self._watchers:
            raise ValueError(f"{watcher.folder} is already being monitored.")
        self._watchers[key] = watcher

    def remove(self, folder: Path) -> None:
        watcher = self._watchers.pop(str(folder), None)
        if watcher is not None:
            watcher.stop()

    def watchers(self) -> list[FolderWatcher]:
        return list(self._watchers.values())

    def get(self, folder: Path) -> FolderWatcher | None:
        return self._watchers.get(str(folder))

    def stop_all(self) -> None:
        for watcher in self._watchers.values():
            watcher.stop()

    def __len__(self) -> int:
        return len(self._watchers)
