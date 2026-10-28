"""Adversarial integration tests (audit P0 / quality gates).

These tests attack the gap between preview and move: destinations swapped for
links, competing processes creating files, cross-volume failures, hard-linked
names, randomized collision names, and protected system locations. The expected
outcome everywhere is the same: no overwrite, no deletion, no escape from the
selected root, and the source left exactly where it was on any refusal.
"""

from __future__ import annotations

import errno
import os
import random
from pathlib import Path

import pytest

from smart_file_organizer.core import fileops
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.fileops import move_no_overwrite
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import ScanError, scan_folder
from smart_file_organizer.utils.paths import protected_root_reason, unique_path

from .conftest import make_file, symlinks_supported


def test_destination_folder_swapped_for_a_symlink_after_preview_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside"
    outside.mkdir()
    source = make_file(workspace / "report.pdf", b"payload")
    plan = build_plan(scan_folder(workspace, options, classifier), classifier)
    assert plan.ready, "the preview must propose a move before the swap"

    # An attacker (or another program) replaces the destination folder with a
    # link out of the selected root between preview and execution.
    destination_dir = workspace / "Documents"
    os.symlink(outside, destination_dir, target_is_directory=True)

    outcome = execute_plan(plan, history=history)

    assert outcome.success_count == 0
    assert source.read_bytes() == b"payload", "the source must be left in place"
    assert not (outside / "report.pdf").exists(), "nothing may be written through the link"


def test_competing_process_creating_the_destination_never_gets_overwritten(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    source = make_file(workspace / "report.pdf", b"incoming")
    plan = build_plan(scan_folder(workspace, options, classifier), classifier)

    # Another process takes the planned destination after the preview.
    rival = make_file(workspace / "Documents" / "report.pdf", b"rival - do not replace")

    outcome = execute_plan(plan, history=history)  # default strategy: rename

    assert rival.read_bytes() == b"rival - do not replace", "the competing file must be untouched"
    assert outcome.success_count == 1
    assert (workspace / "Documents" / "report (2).pdf").read_bytes() == b"incoming"
    assert not source.exists()


def test_cross_volume_failure_leaves_the_source_untouched(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_file(workspace / "report.pdf", b"payload")
    destination = workspace / "elsewhere" / "report.pdf"
    destination.parent.mkdir()

    def fail_cross_volume(*args: object, **kwargs: object) -> None:
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(fileops.os, "link", fail_cross_volume)
    if os.name == "nt":  # the Windows fallback rename would also fail across volumes
        monkeypatch.setattr(fileops.os, "rename", fail_cross_volume)

    with pytest.raises(OSError):
        move_no_overwrite(source, destination)

    assert source.read_bytes() == b"payload", "the source is never deleted before a verified destination exists"
    assert not destination.exists()


def test_two_hard_links_to_one_file_are_moved_as_two_names_without_loss(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    first = make_file(workspace / "a.pdf", b"shared payload")
    second = workspace / "b.pdf"
    try:
        os.link(first, second)
    except OSError:
        pytest.skip("Hard links are not supported in this environment")

    plan = build_plan(scan_folder(workspace, options, classifier), classifier)
    outcome = execute_plan(plan, history=history)

    assert outcome.failed_count == 0
    assert (workspace / "Documents" / "a.pdf").read_bytes() == b"shared payload"
    assert (workspace / "Documents" / "b.pdf").read_bytes() == b"shared payload"


def test_collision_names_are_unique_for_randomized_existing_names(tmp_path: Path) -> None:
    """Property-style check (seeded, no external dependency): unique_path never
    returns an occupied name and never repeats itself, whatever is on disk."""
    rng = random.Random(20261026)
    folder = tmp_path / "crowded"
    folder.mkdir()
    stems = ["report", "photo 2026", "a", "archive.tar", "no-extension"]
    for _ in range(200):
        stem = rng.choice(stems)
        suffix = rng.choice(["", ".pdf", f" ({rng.randint(2, 9)}).pdf"])
        (folder / f"{stem}{suffix}").touch()
    seen: set[Path] = set()
    for _ in range(200):
        stem = rng.choice(stems)
        candidate = unique_path(folder / f"{stem}.pdf")
        assert not candidate.exists(), "unique_path returned an occupied name"
        assert candidate not in seen, "unique_path returned the same name twice"
        seen.add(candidate)
        candidate.touch()  # occupy it, like a competing process would


def test_drive_root_is_a_protected_location() -> None:
    anchor = Path(Path.cwd().anchor)
    assert protected_root_reason(anchor) is not None
    with pytest.raises(ScanError, match="protected"):
        scan_folder(anchor, ScanOptions())


def test_user_and_temporary_folders_are_not_protected(tmp_path: Path) -> None:
    assert protected_root_reason(tmp_path) is None
    assert scan_folder(tmp_path, ScanOptions()).root == tmp_path


def test_destination_folder_swapped_during_journaling_is_refused(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    from smart_file_organizer.core.models import OP_MOVE

    outside = tmp_path / "outside-swap"
    outside.mkdir()
    source = make_file(workspace / "report.pdf", b"payload")
    plan = build_plan(scan_folder(workspace, options, classifier), classifier)
    destination_dir = workspace / "Documents"
    real_record = history.record_operation

    def record_then_swap(*args, **kwargs):
        operation_id = real_record(*args, **kwargs)
        if kwargs.get("op") == OP_MOVE or (len(args) > 1 and args[1] == OP_MOVE):
            destination_dir.rmdir()
            os.symlink(outside, destination_dir, target_is_directory=True)
        return operation_id

    monkeypatch.setattr(history, "record_operation", record_then_swap)
    outcome = execute_plan(plan, history=history)

    assert outcome.success_count == 0
    assert outcome.skipped_count == 1
    assert source.read_bytes() == b"payload"
    assert not (outside / "report.pdf").exists()


def test_operation_lock_blocks_a_second_batch_on_the_same_folder(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    from smart_file_organizer.core.operation_guard import operation_lock

    source = make_file(workspace / "report.pdf", b"payload")
    plan = build_plan(scan_folder(workspace, options, classifier), classifier)
    with operation_lock(workspace) as acquired:
        assert acquired
        outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert outcome.skipped_count == 1
    assert "already running" in outcome.outcomes[0].reason
    assert source.exists()


def test_database_failure_after_a_successful_move_keeps_it_discoverable(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sqlite3

    from smart_file_organizer.core.models import STATUS_PENDING, STATUS_SUCCESS

    make_file(workspace / "report.pdf", b"payload")
    plan = build_plan(scan_folder(workspace, options, classifier), classifier)
    real_update = history.update_operation

    def fail_success_write(operation_id, status, **kwargs):
        if status == STATUS_SUCCESS:
            raise sqlite3.OperationalError("database is locked")
        return real_update(operation_id, status, **kwargs)

    monkeypatch.setattr(history, "update_operation", fail_success_write)
    outcome = execute_plan(plan, history=history)

    assert outcome.success_count == 1
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"payload"
    operations = history.list_operations(outcome.batch_id)
    moves = [op for op in operations if op.destination_path and op.destination_path.endswith("report.pdf")]
    assert moves and moves[0].status == STATUS_PENDING, "the unrecorded move stays pending for restart recovery"
