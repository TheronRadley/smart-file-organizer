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
* **Stop means stop.** A stop request is propagated into the scan and into the
  move batch, so an in-flight poll cannot move a file after Stop was pressed.
  The worker thread handle is kept until the thread has really exited, which
  makes it impossible for a later Start to run a second watcher thread.
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
    STATUS_SKIPPED,
    BatchOutcome,
    OrganizePlan,
    PlannedOperation,
    ScanOptions,
    ScanResult,
)
from smart_file_organizer.core.organizer import CANCELLED_REASON, build_plan, execute_plan
from smart_file_organizer.core.scanner import ScanError, scan_folder

logger = logging.getLogger(__name__)

WATCHER_STOPPED = "stopped"
WATCHER_RUNNING = "running"
WATCHER_PAUSED = "paused"
WATCHER_STOPPING = "stopping"
MAX_EVENTS = 200

FileKey = tuple[str, int, int, str | None]
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
    return (str(operation.source), operation.size, operation.modified_ns, operation.file_id)


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
        # Guards the run lifecycle: the worker thread handle, its stop event and the status.
        self._control_lock = threading.Lock()
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
    def is_thread_alive(self) -> bool:
        """True while a worker thread exists, including one that is still winding down."""
        with self._control_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def pending(self) -> list[PendingMove]:
        with self._lock:
            return list(self._state.pending)

    @property
    def events(self) -> list[WatchEvent]:
        with self._control_lock:
            return list(self._events)

    def _log(self, message: str) -> None:
        with self._lock:
            self._events.append(WatchEvent(self._clock(), message))
        logger.info("[watch %s] %s", self.folder.name, message)

    # ----- polling ------------------------------------------------------

    def poll_once(self, cancel_event: threading.Event | None = None) -> list[BatchOutcome]:
        """Scan once, update pending moves and, if auto-apply is on, act on stable files.

        ``cancel_event`` aborts the poll as soon as it is set: the scan is
        interrupted, no plan is applied and no file is moved.

        Returns the batch outcomes produced by this poll (empty unless auto-apply moved files).
        """
        cancel = cancel_event if cancel_event is not None else self._current_stop_event()
        if cancel.is_set():
            return []

        now = self._clock()
        if not self.folder.is_dir():
            self.last_error = "The monitored folder no longer exists."
            self._log(self.last_error)
            return []

        try:
            scan = scan_folder(self.folder, self.config.scan_options, cancel_event=cancel)
        except ScanError as exc:
            self.last_error = str(exc)
            self._log(self.last_error)
            return []
        if cancel.is_set() or scan.cancelled:
            # Stopped mid-scan: the entries are incomplete, so nothing may be moved.
            return []

        plan = self._build_plan(scan)
        stable = self._stable_operations(plan, now)

        with self._lock:
            self._state.pending = [PendingMove(op.source, op.destination, op.size) for op in stable]
            self._state.pending_keys = {_key(op) for op in stable}

        if not self.config.auto_apply or not stable:
            return []
        if cancel.is_set():
            # Stop was pressed while the preview was being built; do not move anything.
            return []
        return [self._apply(plan, stable, cancel)]

    def apply_pending(self) -> BatchOutcome | None:
        """Apply the moves that were listed as pending, if they are still unchanged."""
        with self._lock:
            approved = set(self._state.pending_keys)
        if not approved:
            return None
        scan = scan_folder(self.folder, self.config.scan_options)
        plan = self._build_plan(scan)
        operations = [op for op in plan.ready if _key(op) in approved]
        if not operations:
            self._log("Pending files changed or disappeared; nothing was moved.")
            return None
        return self._apply(plan, operations, self._current_stop_event())

    def _build_plan(self, scan: ScanResult) -> OrganizePlan:
        return build_plan(
            scan,
            self._classifier,
            organize_by=self.config.organize_by,
            collision_strategy=self.config.collision_strategy,
            disabled_categories=self.config.disabled_categories,
        )

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

    def _apply(self, plan: OrganizePlan, operations: list[PlannedOperation], cancel: threading.Event) -> BatchOutcome:
        outcome = execute_plan(
            plan,
            history=self._history,
            selected=[op.index for op in operations],
            cancel_event=cancel,
            kind=BATCH_WATCH,
        )
        results = {result.index: result for result in outcome.outcomes}
        with self._lock:
            for operation in operations:
                result = results.get(operation.index)
                if result is not None and result.status == STATUS_SKIPPED and result.reason == CANCELLED_REASON:
                    # The batch was stopped before reaching this file. Leave it
                    # unhandled so a later Start still organizes it.
                    continue
                # Handled whether or not the move succeeded, so failures are not retried every poll.
                self._state.handled.add(_key(operation))
                self._state.observed.pop(_key(operation), None)
            self._state.pending = []
            self._state.pending_keys = set()
        moved = outcome.success_count
        if outcome.cancelled:
            self._log(f"Stopped during the batch; {moved} file(s) moved before the stop.")
        else:
            self._log(f"Organized {moved} file(s), skipped {outcome.skipped_count}, failed {outcome.failed_count}.")
        return outcome

    # ----- thread control ------------------------------------------------

    def start(self) -> None:
        with self._control_lock:
            thread = self._thread
            if thread is not None and thread.is_alive():
                # Never run two worker threads for one folder: a thread that is
                # still winding down could move files while the new one scans.
                # Nothing is changed here; the caller is told to try again.
                stopping = self._status == WATCHER_STOPPING
                worker = None
            else:
                stopping = False
                self._thread = None
                self._stop_event = threading.Event()
                self._resume_event.set()
                self._status = WATCHER_RUNNING
                worker = threading.Thread(target=self._run, args=(self._stop_event,), name=f"watch-{self.folder.name}")
                worker.daemon = True
                self._thread = worker
        if worker is None:
            if stopping:
                self._log("Still stopping; monitoring restarts once the current check has finished.")
            return
        self._log(
            "Monitoring started (auto-apply on)." if self.config.auto_apply else "Monitoring started (preview only)."
        )
        worker.start()

    def pause(self) -> None:
        with self._control_lock:
            if self._status != WATCHER_RUNNING:
                return
            self._resume_event.clear()
            self._status = WATCHER_PAUSED
        self._log("Paused.")

    def resume(self) -> None:
        with self._control_lock:
            if self._status != WATCHER_PAUSED:
                return
            self._resume_event.set()
            self._status = WATCHER_RUNNING
        self._log("Resumed.")

    def stop(self, timeout: float = 10.0) -> None:
        """Stop monitoring without blocking the caller for longer than ``timeout``.

        Cancellation reaches the scan and the move batch, so no file is moved
        after this returns. If the worker thread has not finished within
        ``timeout`` the status becomes ``stopping`` and the thread handle is
        kept: a new thread is only started once the old one has exited.
        """
        with self._control_lock:
            thread = self._thread
            stop_event = self._stop_event
            was_active = self._status != WATCHER_STOPPED
        stop_event.set()
        self._resume_event.set()

        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                with self._control_lock:
                    self._status = WATCHER_STOPPING
                self._log("Stopping; the current check is finishing and no further files will be moved.")
                return
        # The worker exited and _finish_run() logged the stop, unless it never started.
        if thread is None and was_active:
            with self._control_lock:
                self._stop_event = threading.Event()
                self._status = WATCHER_STOPPED
            self._log("Monitoring stopped.")

    def _current_stop_event(self) -> threading.Event:
        with self._control_lock:
            return self._stop_event

    def _finish_run(self, stop_event: threading.Event) -> None:
        """Clean up after the worker loop exits, whoever asked it to stop."""
        current = threading.current_thread()
        with self._control_lock:
            if self._thread is current:
                self._thread = None
            if self._stop_event is stop_event:
                self._stop_event = threading.Event()
            self._status = WATCHER_STOPPED
        self._log("Monitoring stopped.")

    def _run(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                self._resume_event.wait()
                if stop_event.is_set():
                    break
                try:
                    self.poll_once(stop_event)
                    self.last_error = None
                except (OSError, sqlite3.Error) as exc:
                    # Logged with its traceback and surfaced in the UI; the watcher keeps running.
                    logger.exception("Monitoring poll failed for %s", self.folder)
                    self.last_error = f"Monitoring hit an error and will retry: {exc.__class__.__name__}"
                    self._log(self.last_error)
                stop_event.wait(self.config.interval_seconds)
        finally:
            self._finish_run(stop_event)


class WatchManager:
    """Owns the set of monitored folders for the application."""

    def __init__(self) -> None:
        self._watchers: dict[str, FolderWatcher] = {}

    def add(self, watcher: FolderWatcher) -> None:
        key = str(watcher.folder)
        if key in self._watchers:
            raise ValueError(f"{watcher.folder} is already being monitored.")
        self._watchers[key] = watcher

    def remove(self, folder: Path, timeout: float = 10.0) -> None:
        watcher = self._watchers.pop(str(folder), None)
        if watcher is not None:
            watcher.stop(timeout)

    def watchers(self) -> list[FolderWatcher]:
        return list(self._watchers.values())

    def get(self, folder: Path) -> FolderWatcher | None:
        return self._watchers.get(str(folder))

    def stop_all(self, timeout: float = 10.0) -> None:
        """Stop every watcher, waiting at most ``timeout`` seconds for each.

        A watcher that needs longer keeps winding down on its own: cancellation
        has already reached it, so it moves nothing further.
        """
        for watcher in self._watchers.values():
            watcher.stop(timeout)

    def __len__(self) -> int:
        return len(self._watchers)
