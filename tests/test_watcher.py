import os
import threading
import time
from pathlib import Path

import pytest

from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.watcher import (
    WATCHER_PAUSED,
    WATCHER_RUNNING,
    WATCHER_STOPPED,
    WATCHER_STOPPING,
    FolderWatcher,
    WatchConfig,
    WatchManager,
)

from .conftest import make_file


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _config(auto_apply: bool, options: ScanOptions) -> WatchConfig:
    return WatchConfig(
        organize_by="type",
        collision_strategy="rename",
        recursive=False,
        auto_apply=auto_apply,
        scan_options=options,
        stable_seconds=10,
        interval_seconds=1,
    )


def _watcher(
    folder: Path, auto_apply: bool, options: ScanOptions, history: HistoryRepository, clock: FakeClock
) -> FolderWatcher:
    return FolderWatcher(folder, _config(auto_apply, options), classifier=Classifier(), history=history, clock=clock)


def test_new_files_wait_until_stable(workspace: Path, options: ScanOptions, history: HistoryRepository) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, False, options, history, clock)
    make_file(workspace / "new.pdf", mtime=int(clock.now))
    watcher.poll_once()
    assert watcher.pending == []
    clock.now += 11
    watcher.poll_once()
    assert [p.source.name for p in watcher.pending] == ["new.pdf"]
    assert (workspace / "new.pdf").exists(), "preview-only mode must not move files"


def test_partially_written_file_resets_the_timer(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    target = make_file(workspace / "copying.zip", b"a" * 10, mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 8
    target.write_bytes(b"a" * 20)  # still being copied
    os.utime(target, (int(clock.now), int(clock.now)))
    clock.now += 8
    assert watcher.poll_once() == []
    assert target.exists()


def test_auto_apply_organizes_stable_files(workspace: Path, options: ScanOptions, history: HistoryRepository) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "song.mp3", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    outcomes = watcher.poll_once()
    assert len(outcomes) == 1 and outcomes[0].success_count == 1
    assert (workspace / "Audio" / "song.mp3").exists()
    assert history.list_batches()[0].kind == "watch"


def test_repeated_polls_do_not_reprocess_handled_files(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "a.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    for _ in range(5):
        clock.now += 11
        assert watcher.poll_once() == []
    assert len(history.list_batches()) == 1


def test_organized_destinations_never_trigger_new_moves(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "Documents" / "already.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 60
    assert watcher.poll_once() == []
    assert watcher.pending == []
    assert (workspace / "Documents" / "already.pdf").exists()


def test_failed_files_are_not_retried_every_poll(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import organizer

    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "locked.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11

    def refuse(source: Path, destination: Path, **kwargs) -> None:
        raise PermissionError(13, "Permission denied", str(source))

    monkeypatch.setattr(organizer, "move_no_overwrite", refuse)
    first = watcher.poll_once()
    assert first[0].failed_count == 1
    monkeypatch.undo()
    assert watcher.poll_once() == []


def test_apply_pending_only_moves_approved_unchanged_files(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, False, options, history, clock)
    make_file(workspace / "one.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    outcome = watcher.apply_pending()
    assert outcome is not None and outcome.success_count == 1
    assert (workspace / "Documents" / "one.pdf").exists()


def test_missing_folder_is_reported(tmp_path: Path, options: ScanOptions, history: HistoryRepository) -> None:
    watcher = _watcher(tmp_path / "gone", False, options, history, FakeClock(0))
    assert watcher.poll_once() == []
    assert "no longer exists" in (watcher.last_error or "")


def test_lifecycle_controls_update_status(workspace: Path, options: ScanOptions, history: HistoryRepository) -> None:
    watcher = _watcher(workspace, False, options, history, FakeClock(0))
    assert watcher.status == WATCHER_STOPPED
    watcher.start()
    assert watcher.status == WATCHER_RUNNING
    watcher.pause()
    assert watcher.status == WATCHER_PAUSED
    watcher.resume()
    assert watcher.status == WATCHER_RUNNING
    watcher.stop()
    assert watcher.status == WATCHER_STOPPED


def test_manager_rejects_duplicate_folders(workspace: Path, options: ScanOptions, history: HistoryRepository) -> None:
    manager = WatchManager()
    manager.add(_watcher(workspace, False, options, history, FakeClock(0)))
    with pytest.raises(ValueError):
        manager.add(_watcher(workspace, False, options, history, FakeClock(0)))
    manager.remove(workspace)
    assert len(manager) == 0


# ----- stopping must really stop ------------------------------------------


def _worker_threads(folder: Path) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == f"watch-{folder.name}"]


def _wait_until(predicate, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_stop_during_a_blocked_scan_never_moves_files(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    target = make_file(workspace / "late.pdf", mtime=int(clock.now))
    clock.now += 60  # long since stable: only the scan is holding the poll back

    entered = threading.Event()
    release = threading.Event()
    real_scan = watcher_module.scan_folder

    def blocked_scan(*args, **kwargs):
        entered.set()
        release.wait(10)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(watcher_module, "scan_folder", blocked_scan)
    watcher.start()
    assert entered.wait(5), "the worker never reached the scan"

    started = time.monotonic()
    watcher.stop(timeout=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 5, f"stop() blocked the caller for {elapsed:.1f}s"
    assert watcher.status == WATCHER_STOPPING
    assert watcher.is_thread_alive, "a thread that has not exited must stay visible"

    release.set()
    assert _wait_until(lambda: not watcher.is_thread_alive)
    assert watcher.status == WATCHER_STOPPED
    assert target.exists(), "Stop was pressed during the scan, so nothing may be moved"
    assert not (workspace / "Documents" / "late.pdf").exists()
    assert history.list_batches() == []


def test_a_second_start_never_runs_two_worker_threads(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    entered = threading.Event()
    release = threading.Event()
    real_scan = watcher_module.scan_folder

    def blocked_scan(*args, **kwargs):
        entered.set()
        release.wait(10)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(watcher_module, "scan_folder", blocked_scan)
    watcher.start()
    assert entered.wait(5)
    watcher.stop(timeout=0.2)
    assert watcher.status == WATCHER_STOPPING

    watcher.start()  # must refuse, not spawn a second thread
    assert len(_worker_threads(workspace)) == 1
    assert any("Still stopping" in event.message for event in watcher.events)

    release.set()
    assert _wait_until(lambda: not watcher.is_thread_alive)
    watcher.start()
    assert len(_worker_threads(workspace)) == 1
    assert watcher.status == WATCHER_RUNNING
    watcher.stop(timeout=5)
    assert watcher.status == WATCHER_STOPPED


def test_files_not_reached_before_a_stop_are_still_organized_later(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "a.pdf", mtime=int(clock.now))
    make_file(workspace / "b.pdf", mtime=int(clock.now))
    real_execute = watcher_module.execute_plan

    def execute_then_cancel(plan, **kwargs):
        cancel = kwargs.get("cancel_event")

        def progress(position: int, total: int) -> None:
            if position == 1 and cancel is not None:
                cancel.set()  # user pressed Stop after the first file

        kwargs["progress"] = progress
        return real_execute(plan, **kwargs)

    monkeypatch.setattr(watcher_module, "execute_plan", execute_then_cancel)
    watcher.poll_once()  # first sighting: the files are not stable yet
    clock.now += 11
    cancel = threading.Event()
    outcomes = watcher.poll_once(cancel)
    assert outcomes[0].cancelled
    assert outcomes[0].success_count == 1

    monkeypatch.undo()
    clock.now += 11
    second = watcher.poll_once()
    assert sum(outcome.success_count for outcome in second) == 1, "the file the stop skipped must not be forgotten"
    assert (workspace / "Documents" / "a.pdf").exists()
    assert (workspace / "Documents" / "b.pdf").exists()


def test_stopped_watcher_poll_does_nothing(workspace: Path, options: ScanOptions, history: HistoryRepository) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, True, options, history, clock)
    make_file(workspace / "a.pdf", mtime=int(clock.now))
    clock.now += 11
    stopped = threading.Event()
    stopped.set()
    assert watcher.poll_once(stopped) == []
    assert (workspace / "a.pdf").exists()


def test_manager_passes_the_stop_timeout_on(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interface stops with a short timeout so a slow folder cannot freeze it."""
    manager = WatchManager()
    watcher = _watcher(workspace, False, options, history, FakeClock(0))
    manager.add(watcher)
    seen: list[float] = []
    monkeypatch.setattr(watcher, "stop", lambda timeout=10.0: seen.append(timeout))

    manager.stop_all(timeout=0.25)
    manager.remove(workspace, timeout=0.5)

    assert seen == [0.25, 0.5]
