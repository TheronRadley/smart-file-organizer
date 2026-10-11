import os
from pathlib import Path

import pytest

from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.watcher import (
    WATCHER_PAUSED,
    WATCHER_RUNNING,
    WATCHER_STOPPED,
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

    def refuse(source: Path, destination: Path) -> None:
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
