"""Polling-based folder automation with immutable approvals and cooperative stop."""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from smart_file_organizer.config import Settings
from smart_file_organizer.core.cancellation import AnyCancellation, Cancellation
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_WATCH,
    BatchOutcome,
    OrganizePlan,
    PlannedOperation,
    ScanOptions,
    ScanResult,
)
from smart_file_organizer.core.organizer import (
    BUSY_REASON,
    CANCELLED_REASON,
    DESTINATION_CHANGED_REASON,
    DESTINATION_RACE_REASON,
    build_plan,
    execute_plan,
)
from smart_file_organizer.core.scanner import ScanError, scan_folder
from smart_file_organizer.utils.paths import normalise

logger = logging.getLogger(__name__)

WATCHER_STOPPED = "stopped"
WATCHER_RUNNING = "running"
WATCHER_PAUSED = "paused"
WATCHER_STOPPING = "stopping"
MAX_EVENTS = 200
_RETRY_REASONS = {BUSY_REASON, CANCELLED_REASON, DESTINATION_CHANGED_REASON, DESTINATION_RACE_REASON}

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

    @classmethod
    def from_settings(cls, settings: Settings) -> WatchConfig:
        return cls(
            organize_by=settings.organize_by,
            collision_strategy=settings.collision_strategy,
            recursive=settings.watch_recursive,
            auto_apply=settings.watch_auto_apply,
            scan_options=ScanOptions(
                recursive=settings.watch_recursive,
                excluded_folders=tuple(settings.excluded_folders),
                excluded_patterns=tuple(settings.excluded_patterns),
            ),
            disabled_categories=frozenset(settings.disabled_categories),
            interval_seconds=float(settings.watch_interval_seconds),
        )


@dataclass(frozen=True)
class PendingMove:
    source: Path
    destination: Path | None
    size: int
    modified_ns: int
    file_id: str | None

    @classmethod
    def from_operation(cls, operation: PlannedOperation) -> PendingMove:
        return cls(operation.source, operation.destination, operation.size, operation.modified_ns, operation.file_id)


@dataclass(frozen=True)
class PendingPreview:
    """The exact rows rendered for approval, bound to one watcher run and ruleset."""

    folder: Path
    root_identity: str | None
    generation: int
    run: threading.Event = field(repr=False)
    moves: tuple[PendingMove, ...] = ()


@dataclass(frozen=True)
class WatchEvent:
    timestamp: float
    message: str

    @property
    def text(self) -> str:
        return f"{datetime.fromtimestamp(self.timestamp):%H:%M:%S}  {self.message}"


@dataclass(frozen=True)
class _Rules:
    config: WatchConfig
    classifier: Classifier
    generation: int
    invalidated: threading.Event = field(default_factory=threading.Event)


@dataclass
class _WatchState:
    observed: dict[FileKey, float] = field(default_factory=dict)
    handled: set[FileKey] = field(default_factory=set)
    pending: tuple[PendingMove, ...] = ()
    pending_root_identity: str | None = None


def _key(operation: PlannedOperation) -> FileKey:
    return (str(operation.source), operation.size, operation.modified_ns, operation.file_id)


class FolderWatcher:
    """Monitors one folder; state and lifecycle can be observed from the UI thread."""

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
        self._rules = _Rules(config, classifier, 0)
        self._history = history
        self._clock = clock
        self._state = _WatchState()
        self._lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._resume_event = threading.Event()
        self._resume_event.set()
        self._thread: threading.Thread | None = None
        self._manual_jobs = 0
        self._manual_idle = threading.Event()
        self._manual_idle.set()
        self._status = WATCHER_STOPPED
        self._events: deque[WatchEvent] = deque(maxlen=MAX_EVENTS)
        self.last_error: str | None = None

    @property
    def status(self) -> str:
        with self._control_lock:
            if self._stop_event.is_set() and self._active_locked():
                return WATCHER_STOPPING
            return self._status

    @property
    def is_thread_alive(self) -> bool:
        with self._control_lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def has_active_work(self) -> bool:
        with self._control_lock:
            return self._active_locked()

    def _active_locked(self) -> bool:
        return self._manual_jobs > 0 or (self._thread is not None and self._thread.is_alive())

    @property
    def pending(self) -> list[PendingMove]:
        return list(self.pending_preview().moves)

    def pending_preview(self) -> PendingPreview:
        run = self._current_stop_event()
        with self._lock:
            return PendingPreview(
                self.folder,
                self._state.pending_root_identity,
                self._rules.generation,
                run,
                self._state.pending if not run.is_set() else (),
            )

    @property
    def events(self) -> list[WatchEvent]:
        with self._lock:
            return list(self._events)

    def _log(self, message: str) -> None:
        with self._lock:
            self._events.append(WatchEvent(self._clock(), message))
        logger.info("[watch %s] %s", self.folder.name, message)

    def update_rules(self, config: WatchConfig, classifier: Classifier) -> None:
        """Cancel work from the old policy and discard approvals tied to it."""
        with self._lock:
            if self._rules.config == config and self._rules.classifier.has_same_rules(classifier):
                return
            self._rules.invalidated.set()
            self._rules = _Rules(config, classifier, self._rules.generation + 1)
            self._state = _WatchState(handled=self._state.handled)

    def _current_rules(self) -> _Rules:
        with self._lock:
            return self._rules

    def poll_once(self, cancel_event: Cancellation | None = None) -> list[BatchOutcome]:
        """Scan with one policy snapshot; act only on files stable under that policy."""
        rules = self._current_rules()
        cancel = self._cancel_flag(rules, self._current_stop_event(), cancel_event)
        if cancel.is_set():
            return []
        now = self._clock()
        if not self.folder.is_dir():
            self.last_error = "The monitored folder no longer exists."
            self._log(self.last_error)
            return []
        try:
            scan = scan_folder(self.folder, rules.config.scan_options, rules.classifier, cancel_event=cancel)
        except ScanError as exc:
            self.last_error = str(exc)
            self._log(self.last_error)
            return []
        if cancel.is_set() or scan.cancelled:
            return []
        self.last_error = None
        plan = self._build_plan(scan, rules)
        with self._lock:
            if cancel.is_set():
                return []
            stable = self._stable_operations(plan, now, rules.config.stable_seconds)
            self._state.pending = (
                () if rules.config.auto_apply else tuple(PendingMove.from_operation(op) for op in stable)
            )
            self._state.pending_root_identity = plan.root_identity
        if not rules.config.auto_apply or not stable or cancel.is_set():
            return []
        return [self._apply(plan, stable, rules, cancel, exact_destinations=False)]

    def apply_pending(
        self, approved: PendingPreview, *, cancel_event: Cancellation | None = None
    ) -> BatchOutcome | None:
        """Revalidate only the supplied rows; never substitute new ones or rename late collisions."""
        with self._control_lock:
            run = self._stop_event
            if not approved.moves or run.is_set():
                return None
            if approved.folder != self.folder or approved.run is not run:
                return None
            self._manual_jobs += 1
            self._manual_idle.clear()
        try:
            return self._apply_approved(approved, run, cancel_event)
        finally:
            with self._control_lock:
                self._manual_jobs -= 1
                if not self._manual_jobs:
                    self._manual_idle.set()

    def _apply_approved(
        self, approved: PendingPreview, run: threading.Event, external_cancel: Cancellation | None
    ) -> BatchOutcome | None:
        rules = self._current_rules()
        cancel = self._cancel_flag(rules, run, external_cancel)
        if cancel.is_set():
            return None
        if approved.generation != rules.generation:
            self._log("Monitoring rules changed. Review the new preview before approving it.")
            return None
        scan = scan_folder(self.folder, rules.config.scan_options, rules.classifier, cancel_event=cancel)
        if cancel.is_set() or scan.cancelled:
            return None
        if scan.root_identity != approved.root_identity:
            self._log("The monitored folder changed. Nothing was moved; review a fresh preview.")
            return None
        plan = self._build_plan(scan, rules)
        approved_moves = set(approved.moves)
        operations = [op for op in plan.ready if PendingMove.from_operation(op) in approved_moves]
        matched = {PendingMove.from_operation(op) for op in operations}
        drifted = approved_moves - matched
        if drifted:
            with self._lock:
                if rules is self._rules and self._state.pending_root_identity == approved.root_identity:
                    self._discard_pending(drifted)
            self._log(f"{len(drifted)} approved file(s) or destinations changed; review a fresh preview.")
        if not operations or cancel.is_set():
            return None
        return self._apply(plan, operations, rules, cancel, exact_destinations=True)

    @staticmethod
    def _build_plan(scan: ScanResult, rules: _Rules) -> OrganizePlan:
        return build_plan(
            scan,
            rules.classifier,
            organize_by=rules.config.organize_by,
            collision_strategy=rules.config.collision_strategy,
            disabled_categories=rules.config.disabled_categories,
        )

    def _stable_operations(self, plan: OrganizePlan, now: float, stable_seconds: float) -> list[PlannedOperation]:
        """Called while holding the state lock."""
        stable: list[PlannedOperation] = []
        live_keys: set[FileKey] = set()
        for operation in plan.ready:
            key = _key(operation)
            live_keys.add(key)
            if key in self._state.handled:
                continue
            first_seen = self._state.observed.setdefault(key, now)
            settled = now - first_seen >= stable_seconds
            quiet = now - operation.modified_ns / 1_000_000_000 >= stable_seconds
            if settled and quiet:
                stable.append(operation)
        for key in self._state.observed.keys() - live_keys:
            del self._state.observed[key]
        return stable

    def _discard_pending(self, moves: set[PendingMove]) -> None:
        self._state.pending = tuple(item for item in self._state.pending if item not in moves)

    def _apply(
        self,
        plan: OrganizePlan,
        operations: list[PlannedOperation],
        rules: _Rules,
        cancel: Cancellation,
        *,
        exact_destinations: bool,
    ) -> BatchOutcome:
        outcome = execute_plan(
            plan,
            history=self._history,
            selected=[op.index for op in operations],
            cancel_event=cancel,
            kind=BATCH_WATCH,
            exact_destinations=exact_destinations,
        )
        results = {result.index: result for result in outcome.outcomes}
        with self._lock:
            if rules is self._rules:
                discard: set[PendingMove] = set()
                for operation in operations:
                    result = results.get(operation.index)
                    if result is None:
                        continue
                    discard.add(PendingMove.from_operation(operation))
                    if result.reason not in _RETRY_REASONS:
                        # A locked file should not generate the same error every poll.
                        self._state.handled.add(_key(operation))
                        self._state.observed.pop(_key(operation), None)
                if self._state.pending_root_identity == plan.root_identity:
                    self._discard_pending(discard)
        self._log(
            f"Organized {outcome.success_count} file(s), "
            f"skipped {outcome.skipped_count}, failed {outcome.failed_count}."
        )
        if outcome.needs_recovery:
            self._log("Some outcomes could not be recorded. Check Recovery guidance in History.")
        return outcome

    @staticmethod
    def _cancel_flag(rules: _Rules, stop: threading.Event, external: Cancellation | None) -> Cancellation:
        flags = (rules.invalidated, stop) if external is None else (rules.invalidated, stop, external)
        return AnyCancellation(flags)

    def start(self) -> bool:
        with self._control_lock:
            if self._active_locked():
                stopping = self._stop_event.is_set()
                started = False
            else:
                stopping = False
                self._stop_event = threading.Event()
                self._resume_event.set()
                with self._lock:
                    self._state.pending = ()
                    self._state.pending_root_identity = None
                    self._state.observed.clear()
                    self._events.append(WatchEvent(self._clock(), "Monitoring started."))
                self._status = WATCHER_RUNNING
                self._thread = threading.Thread(
                    target=self._run, args=(self._stop_event,), name=f"watch-{self.folder.name}", daemon=True
                )
                self._thread.start()
                started = True
        if stopping:
            self._log("Still stopping; try starting again once the current check has finished.")
        return started

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

    def request_stop(self) -> None:
        """Signal this watcher (including manual approvals) without joining its worker."""
        with self._control_lock:
            self._stop_event.set()
            self._resume_event.set()
            if self._status in (WATCHER_RUNNING, WATCHER_PAUSED):
                self._status = WATCHER_STOPPING

    def wait_stopped(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._control_lock:
            thread = self._thread
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        self._manual_idle.wait(max(0.0, deadline - time.monotonic()))
        return not self.has_active_work

    def stop(self, timeout: float = 10.0) -> None:
        self.request_stop()
        self.wait_stopped(timeout)

    def _current_stop_event(self) -> threading.Event:
        with self._control_lock:
            return self._stop_event

    def _run(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                self._resume_event.wait()
                if stop.is_set():
                    break
                try:
                    self.poll_once(stop)
                except (OSError, ValueError, sqlite3.Error) as exc:
                    logger.exception("Monitoring poll failed for %s", self.folder)
                    self.last_error = f"Monitoring hit an error and will retry: {exc.__class__.__name__}"
                    self._log(self.last_error)
                stop.wait(self._current_rules().config.interval_seconds)
        finally:
            try:
                self._history.close_thread_connection()
            finally:
                with self._control_lock:
                    stop.set()
                    self._status = WATCHER_STOPPED
                self._log("Monitoring stopped.")


class WatchManager:
    """Owns the set of monitored folders for the application."""

    def __init__(self) -> None:
        self._watchers: dict[str, FolderWatcher] = {}

    def add(self, watcher: FolderWatcher) -> None:
        if self.get(watcher.folder) is not None:
            raise ValueError(f"{watcher.folder} is already being monitored.")
        self._watchers[_folder_key(watcher.folder)] = watcher

    def remove(self, folder: Path, timeout: float = 10.0) -> bool:
        watcher = self.get(folder)
        if watcher is None:
            return True
        watcher.request_stop()
        if timeout > 0:
            watcher.wait_stopped(timeout)
        if watcher.has_active_work:
            return False  # Retain the watcher until both poll and manual apply have exited.
        del self._watchers[_folder_key(watcher.folder)]
        return True

    def watchers(self) -> list[FolderWatcher]:
        return list(self._watchers.values())

    def get(self, folder: Path) -> FolderWatcher | None:
        # A renamed or replaced folder must still be removable by the path used to add it.
        watcher = self._watchers.get(_folder_key(folder))
        if watcher is not None or not self._watchers:
            return watcher
        resolved = normalise(folder)
        return next((watcher for watcher in self._watchers.values() if normalise(watcher.folder) == resolved), None)

    def request_stop_all(self) -> None:
        for watcher in self._watchers.values():
            watcher.request_stop()

    def all_stopped(self) -> bool:
        return all(not watcher.has_active_work for watcher in self._watchers.values())

    def stop_all(self, timeout: float = 10.0) -> bool:
        """Signal every watcher, then wait using a shared timeout (not for Tk callbacks)."""
        self.request_stop_all()
        deadline = time.monotonic() + timeout
        for watcher in self._watchers.values():
            watcher.wait_stopped(max(0.0, deadline - time.monotonic()))
        return self.all_stopped()

    def update_settings(self, settings: Settings) -> None:
        config = WatchConfig.from_settings(settings)
        classifier = Classifier.from_settings(settings)
        for watcher in self._watchers.values():
            watcher.update_rules(config, classifier)

    def __len__(self) -> int:
        return len(self._watchers)


def _folder_key(folder: Path) -> str:
    return os.path.normcase(os.path.abspath(folder))
