"""Recovery advice is based on recorded identity, not on paths merely existing."""

import os
from pathlib import Path

import pytest

from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import BATCH_ORGANIZE, OP_MOVE, STATUS_PENDING, STATUS_SUCCESS, UNDO_PENDING
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.recovery import RecoveryError, recovery_steps, unfinished_batches
from smart_file_organizer.core.scanner import file_identity, scan_folder

from .conftest import make_file


def _record(history: HistoryRepository, root: Path, file: Path | None, *, undo: bool = False) -> str:
    batch = history.create_batch(BATCH_ORGANIZE, root)
    stats = file.lstat() if file is not None else None
    operation = history.record_operation(
        batch,
        OP_MOVE,
        source=str(root / "report.pdf"),
        destination=str(root / "Documents" / "report.pdf"),
        file_id=file_identity(stats, file) if stats is not None else None,
        size=stats.st_size if stats is not None else None,
        modified_ns=stats.st_mtime_ns if stats is not None else None,
        status=STATUS_SUCCESS if undo else STATUS_PENDING,
    )
    if undo:
        history.set_undo_result(operation, UNDO_PENDING)
    # Older versions could finalize a batch despite failing to record the outcome.
    history.finish_batch(batch, "completed")
    return batch


def test_verified_original_with_no_destination_can_retry(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf")
    batch = _record(history, workspace, source)
    assert history.recover_interrupted() == 1
    (step,) = recovery_steps(batch, history)
    assert "verified against the recorded file" in step.detail
    assert str(source) in step.detail
    assert "run the preview again" in step.next_action


def test_existing_unrelated_file_is_not_reported_as_a_copy(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf")
    destination = make_file(workspace / "Documents" / "report.pdf")
    batch = _record(history, workspace, source)
    (step,) = recovery_steps(batch, history)
    assert f"Organized '{destination}': unverified" in step.detail
    assert "Check both paths manually" in step.next_action


def test_legacy_hard_links_are_reported_but_not_removed(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf")
    destination = workspace / "Documents" / "report.pdf"
    destination.parent.mkdir()
    try:
        os.link(source, destination)
    except OSError:
        pytest.skip("Hard links are unavailable")
    batch = _record(history, workspace, source)
    (step,) = recovery_steps(batch, history)
    assert "Both entries match the recorded file" in step.next_action
    assert source.exists() and destination.exists()


def test_verified_destination_explains_unrecorded_move(workspace: Path, history: HistoryRepository) -> None:
    destination = make_file(workspace / "Documents" / "report.pdf")
    batch = _record(history, workspace, destination)
    (step,) = recovery_steps(batch, history)
    assert f"Organized '{destination}': verified" in step.detail
    assert "Undo cannot reverse an unrecorded move" in step.next_action


def test_missing_file_needs_manual_search(workspace: Path, history: HistoryRepository) -> None:
    batch = _record(history, workspace, None)
    (step,) = recovery_steps(batch, history)
    assert step.detail.count(": missing.") == 2
    assert "Do not run the batch again" in step.next_action


def test_undo_without_recorded_id_is_unverified(workspace: Path, history: HistoryRepository) -> None:
    destination = make_file(workspace / "Documents" / "report.pdf")
    batch = _record(history, workspace, None, undo=True)
    assert history.recover_interrupted() == 0
    assert history.get_batch(batch).status == "undo_partial"
    (step,) = recovery_steps(batch, history)
    assert f"Organized '{destination}': unverified" in step.detail
    assert "Check both paths manually" in step.next_action


def test_a_clean_batch_needs_no_guidance(workspace: Path, options, classifier, history: HistoryRepository) -> None:
    make_file(workspace / "report.pdf")
    outcome = execute_plan(build_plan(scan_folder(workspace, options), classifier), history=history)
    assert outcome.success_count == 1
    assert recovery_steps(outcome.batch_id, history) == ()
    assert unfinished_batches(history) == []


def test_completed_batch_with_pending_move_is_visible_before_and_after_restart(
    workspace: Path, history: HistoryRepository
) -> None:
    batch = _record(history, workspace, None)
    (pending,) = unfinished_batches(history)
    assert pending.id == batch and pending.status == "completed" and pending.needs_recovery
    assert history.recover_interrupted() == 1
    (interrupted,) = unfinished_batches(history)
    assert interrupted.id == batch and interrupted.status == "interrupted"


def test_unknown_batch_is_reported(history: HistoryRepository) -> None:
    with pytest.raises(RecoveryError, match="no longer exists"):
        recovery_steps("missing", history)


def test_recovery_limit_selects_unfinished_work_not_recent_clean_batches(
    workspace: Path, history: HistoryRepository
) -> None:
    old_batch = _record(history, workspace, None)
    recent = history.create_batch(BATCH_ORGANIZE, workspace)
    history.finish_batch(recent, "completed")
    assert [batch.id for batch in unfinished_batches(history, limit=1)] == [old_batch]


def test_recovery_refuses_to_verify_a_file_edited_after_the_move(workspace: Path, history: HistoryRepository) -> None:
    destination = make_file(workspace / "Documents" / "report.pdf")
    batch = _record(history, workspace, destination)
    destination.write_bytes(b"different content")
    (step,) = recovery_steps(batch, history)
    assert f"Organized '{destination}': unverified" in step.detail
    assert "Check both paths manually" in step.next_action
