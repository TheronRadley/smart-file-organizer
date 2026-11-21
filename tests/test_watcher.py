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
    outcome = watcher.apply_pending(watcher.pending_preview())
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
    assert _wait_until(lambda: len(_worker_threads(workspace)) == 0), "the first worker never finished"
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
    cancel = threading.Event()

    def execute_then_cancel(plan, **kwargs):
        def progress(position: int, total: int) -> None:
            if position == 1:
                cancel.set()  # user pressed Stop after the first file

        kwargs["progress"] = progress
        return real_execute(plan, **kwargs)

    monkeypatch.setattr(watcher_module, "execute_plan", execute_then_cancel)
    watcher.poll_once()  # first sighting: the files are not stable yet
    clock.now += 11
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


def test_manager_signals_every_watcher_before_waiting(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = WatchManager()
    slow = _watcher(workspace / "slow", False, options, history, FakeClock(0))
    other = _watcher(workspace / "other", False, options, history, FakeClock(0))
    manager.add(slow)
    manager.add(other)
    seen = []

    def wait(timeout: float) -> bool:
        assert other.pending_preview().run.is_set() and slow.pending_preview().run.is_set()
        seen.append(timeout)
        return True

    monkeypatch.setattr(slow, "wait_stopped", wait)
    assert manager.stop_all(timeout=0.25)
    assert len(seen) == 1 and 0 <= seen[0] <= 0.25


def test_apply_pending_refuses_a_destination_the_user_never_saw(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, False, options, history, clock)
    make_file(workspace / "one.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    assert [str(p.destination) for p in watcher.pending] == [str(workspace / "Documents" / "one.pdf")]

    # Another program takes the shown destination: a rebuilt plan would now
    # pick "one (2).pdf", which the user never approved.
    make_file(workspace / "Documents" / "one.pdf", b"someone else")

    assert watcher.apply_pending(watcher.pending_preview()) is None
    assert (workspace / "one.pdf").exists(), "the file must not move to an unapproved destination"
    assert not (workspace / "Documents" / "one (2).pdf").exists()
    assert history.list_batches() == []
    # The next poll shows the new destination for renewed approval.
    watcher.poll_once()
    assert [p.destination.name for p in watcher.pending] == ["one (2).pdf"]
    outcome = watcher.apply_pending(watcher.pending_preview())
    assert outcome is not None and outcome.success_count == 1
    assert (workspace / "Documents" / "one (2).pdf").exists()


def test_apply_pending_refuses_a_same_size_same_timestamp_replacement(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, False, options, history, clock)
    original = make_file(workspace / "one.pdf", b"AAAA", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    assert len(watcher.pending) == 1

    # Replace the approved file with a different file that has the same name,
    # the same size, and the same modification time. Only its identity differs.
    # (Created while the original exists, so it cannot reuse its inode.)
    replacement = make_file(workspace / "replacement.bin", b"BBBB", mtime=int(clock.now) - 11)
    os.replace(replacement, original)

    assert watcher.apply_pending(watcher.pending_preview()) is None
    assert (workspace / "one.pdf").read_bytes() == b"BBBB", "the unapproved replacement must not be moved"
    assert not (workspace / "Documents" / "one.pdf").exists()
    assert history.list_batches() == []
    assert not watcher.pending
    watcher.poll_once()
    assert not watcher.pending, "the replacement must settle before it is offered"
    clock.now += 11
    watcher.poll_once()
    assert len(watcher.pending) == 1


def test_failed_poll_error_survives_until_a_successful_poll(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    watcher = _watcher(workspace / "gone", False, options, history, FakeClock(0))
    # Simulate what _run does: poll_once must leave last_error in place (it used
    # to be cleared unconditionally by the worker loop right after the poll).
    assert watcher.poll_once() == []
    assert watcher.last_error is not None and "no longer exists" in watcher.last_error
    (workspace / "gone").mkdir(parents=True)
    watcher.folder = workspace / "gone"
    assert watcher.poll_once() == []
    assert watcher.last_error is None, "only a genuinely successful poll may clear the error"


def test_manager_normalizes_folder_identity_and_keeps_stopping_watchers(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    manager = WatchManager()
    watcher = _watcher(workspace, False, options, history, FakeClock(0))
    manager.add(watcher)
    assert manager.get(workspace / ".") is watcher, "an alternative spelling must resolve to the same watcher"
    with pytest.raises(ValueError):
        manager.add(_watcher(workspace / ".", False, options, history, FakeClock(0)))

    # A watcher that is still stopping must not be discarded.
    watcher._thread = threading.Thread(target=time.sleep, args=(0.4,))
    watcher._thread.start()
    assert manager.remove(workspace, timeout=0.05) is False
    assert manager.get(workspace) is watcher, "the stopping watcher stays registered"
    watcher._thread.join()
    watcher._thread = None
    assert manager.remove(workspace) is True
    assert len(manager) == 0


def test_apply_pending_honours_a_cancelled_task(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_000)
    watcher = _watcher(workspace, False, options, history, clock)
    make_file(workspace / "one.pdf", mtime=int(clock.now))
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    assert len(watcher.pending) == 1
    cancel = threading.Event()
    cancel.set()
    assert watcher.apply_pending(watcher.pending_preview(), cancel_event=cancel) is None
    assert (workspace / "one.pdf").exists()
    assert history.list_batches() == []


def test_stop_all_reports_whether_every_watcher_exited(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    manager = WatchManager()
    manager.add(_watcher(workspace, False, options, history, FakeClock(0)))
    assert manager.stop_all(timeout=1.0) is True


def test_approval_uses_the_displayed_snapshot_not_a_later_poll(
    workspace: Path, options: ScanOptions, history: HistoryRepository
) -> None:
    clock = FakeClock(1_700_000_011)
    watcher = _watcher(workspace, False, options, history, clock)
    make_file(workspace / "displayed.pdf")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    shown = watcher.pending_preview()
    assert [item.source.name for item in shown.moves] == ["displayed.pdf"]

    later = make_file(workspace / "later.pdf")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    assert len(watcher.pending) == 2
    outcome = watcher.apply_pending(shown)
    assert outcome is not None and outcome.success_count == 1
    assert (workspace / "Documents" / "displayed.pdf").exists()
    assert later.exists()
    assert [item.source for item in watcher.pending] == [later]


def test_approved_move_is_skipped_if_destination_changes_after_replanning(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_011)
    watcher = _watcher(workspace, False, options, history, clock)
    source = make_file(workspace / "report.pdf")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    approved = watcher.pending_preview()
    target = workspace / "Documents" / "report.pdf"
    execute = watcher_module.execute_plan

    def occupy_then_execute(plan, **kwargs):
        make_file(target, b"keep this")
        return execute(plan, **kwargs)

    monkeypatch.setattr(watcher_module, "execute_plan", occupy_then_execute)
    outcome = watcher.apply_pending(approved)
    assert outcome is not None and outcome.skipped_count == 1
    assert source.exists() and target.read_bytes() == b"keep this"
    assert not target.with_name("report (2).pdf").exists()


def test_live_settings_update_classification_and_invalidate_approvals(
    workspace: Path, history: HistoryRepository
) -> None:
    from smart_file_organizer.config import Settings

    clock = FakeClock(1_700_000_011)
    manager = WatchManager()
    settings = Settings(extension_overrides={"csv": "Documents"})
    watcher = FolderWatcher(
        workspace,
        WatchConfig.from_settings(settings),
        classifier=Classifier.from_settings(settings),
        history=history,
        clock=clock,
    )
    manager.add(watcher)
    source = make_file(workspace / "report.csv")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    approved = watcher.pending_preview()
    assert approved.moves[0].destination == workspace / "Documents" / source.name

    manager.update_settings(Settings(excluded_patterns=["*.csv"]))
    assert not watcher.pending
    assert watcher.apply_pending(approved) is None
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    assert not watcher.pending and source.exists()


def test_resetting_auto_apply_cancels_a_poll_using_old_rules(
    workspace: Path, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.config import Settings
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_011)
    manager = WatchManager()
    watcher = _watcher(workspace, True, ScanOptions(), history, clock)
    manager.add(watcher)
    source = make_file(workspace / "report.pdf")
    watcher.poll_once()
    clock.now += 11
    scan_folder = watcher_module.scan_folder

    def scan_then_reset(*args, **kwargs):
        scan = scan_folder(*args, **kwargs)
        manager.update_settings(Settings())
        return scan

    monkeypatch.setattr(watcher_module, "scan_folder", scan_then_reset)
    assert watcher.poll_once() == []
    assert source.exists() and history.list_batches() == []


def test_stop_signals_two_live_watchers_without_waiting_for_the_slow_one(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    slow = _watcher(workspace / "slow", False, options, history, FakeClock(0))
    fast = _watcher(workspace / "fast", False, options, history, FakeClock(0))
    manager = WatchManager()
    entered = threading.Event()
    release = threading.Event()
    for watcher in (slow, fast):
        watcher.folder.mkdir()
        manager.add(watcher)
    scan = watcher_module.scan_folder

    def block_first(folder, *args, **kwargs):
        if folder == slow.folder:
            entered.set()
            release.wait(10)
        return scan(folder, *args, **kwargs)

    monkeypatch.setattr(watcher_module, "scan_folder", block_first)
    try:
        slow.start()
        fast.start()
        assert entered.wait(5)
        manager.request_stop_all()
        assert not manager.all_stopped()
        assert _wait_until(lambda: not fast.has_active_work)
        assert slow.status == WATCHER_STOPPING
    finally:
        release.set()
        assert manager.stop_all(timeout=5)


def test_remove_cancels_and_keeps_an_inflight_manual_approval(
    workspace: Path, options: ScanOptions, history: HistoryRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import watcher as watcher_module

    clock = FakeClock(1_700_000_011)
    watcher = _watcher(workspace, False, options, history, clock)
    manager = WatchManager()
    manager.add(watcher)
    source = make_file(workspace / "report.pdf")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    approved = watcher.pending_preview()
    assert approved.moves

    entered = threading.Event()
    release = threading.Event()
    scan = watcher_module.scan_folder
    result = []

    def blocked_scan(*args, **kwargs):
        entered.set()
        release.wait(10)
        return scan(*args, **kwargs)

    monkeypatch.setattr(watcher_module, "scan_folder", blocked_scan)
    task = threading.Thread(target=lambda: result.append(watcher.apply_pending(approved)), daemon=True)
    task.start()
    try:
        assert entered.wait(5)
        assert manager.remove(workspace, timeout=0) is False
        assert not manager.all_stopped()
        assert manager.get(workspace) is watcher
        assert watcher.status == WATCHER_STOPPING and not watcher.start()
    finally:
        release.set()
        task.join(5)
    assert not task.is_alive() and result == [None]
    assert manager.remove(workspace, timeout=0) is True
    assert source.exists() and not history.list_batches()


def test_poll_worker_connection_is_released_at_stop(workspace: Path, options: ScanOptions, history, database) -> None:
    ready = threading.Event()
    watcher = _watcher(workspace, False, options, history, FakeClock(0))
    main_connection = database.connection()

    def poll(_cancel):
        history.list_batches()
        ready.set()
        return []

    watcher.poll_once = poll
    for _ in range(4):
        ready.clear()
        assert watcher.start()
        try:
            assert ready.wait(5)
            assert len(database._connections) == 2
        finally:
            watcher.stop(timeout=5)
        assert not watcher.has_active_work
        assert database._connections == {main_connection}


def test_non_rule_setting_changes_preserve_an_approval(workspace: Path, history: HistoryRepository) -> None:
    from smart_file_organizer.config import Settings

    clock = FakeClock(1_700_000_011)
    manager = WatchManager()
    settings = Settings()
    watcher = FolderWatcher(
        workspace,
        WatchConfig.from_settings(settings),
        classifier=Classifier.from_settings(settings),
        history=history,
        clock=clock,
    )
    manager.add(watcher)
    make_file(workspace / "report.pdf")
    watcher.poll_once()
    clock.now += 11
    watcher.poll_once()
    shown = watcher.pending_preview()
    assert shown.moves
    manager.update_settings(Settings(theme="light"))
    assert watcher.pending_preview() == shown
    outcome = watcher.apply_pending(shown)
    assert outcome is not None and outcome.success_count == 1
