"""Recovery guidance for operations that did not finish cleanly."""

from pathlib import Path

import pytest

from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_ORGANIZE,
    OP_MOVE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    UNDO_FAILED,
    ScanOptions,
)
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.recovery import (
    SEVERITY_ATTENTION,
    RecoveryError,
    recovery_steps,
    unfinished_batches,
)
from smart_file_organizer.core.scanner import scan_folder

from .conftest import make_file


def _interrupted_move(history: HistoryRepository, source: Path, destination: Path) -> tuple[str, int]:
    """Record a move the way a crash would leave it: pending, then marked interrupted."""
    batch_id = history.create_batch(BATCH_ORGANIZE, source.parent)
    operation_id = history.record_operation(
        batch_id,
        OP_MOVE,
        source=str(source),
        destination=str(destination),
        size=4,
        status=STATUS_PENDING,
    )
    assert history.recover_interrupted() == 1
    return batch_id, operation_id


def test_interrupted_move_that_never_happened_says_so(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf", b"data")
    destination = workspace / "Documents" / "report.pdf"
    batch_id, _ = _interrupted_move(history, source, destination)

    (step,) = recovery_steps(batch_id, history)
    assert step.severity == SEVERITY_ATTENTION
    assert "never happened" in step.detail
    assert str(source) in step.detail
    assert "run the preview again" in step.next_action


def test_interrupted_move_that_left_two_copies_names_both(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf", b"data")
    destination = make_file(workspace / "Documents" / "report.pdf", b"data")
    batch_id, _ = _interrupted_move(history, source, destination)

    (step,) = recovery_steps(batch_id, history)
    assert "both recorded locations" in step.detail
    assert str(source) in step.detail and str(destination) in step.detail
    assert "Nothing is deleted for you" in step.next_action


def test_interrupted_move_that_completed_but_was_unrecorded(workspace: Path, history: HistoryRepository) -> None:
    source = workspace / "report.pdf"
    destination = make_file(workspace / "Documents" / "report.pdf", b"data")
    batch_id, _ = _interrupted_move(history, source, destination)

    (step,) = recovery_steps(batch_id, history)
    assert "was not recorded" in step.detail
    assert "Undo cannot reverse" in step.next_action


def test_interrupted_move_with_no_file_anywhere(workspace: Path, history: HistoryRepository) -> None:
    batch_id, _ = _interrupted_move(history, workspace / "report.pdf", workspace / "Documents" / "report.pdf")

    (step,) = recovery_steps(batch_id, history)
    assert "neither recorded location" in step.detail
    assert "report.pdf" in step.next_action
    assert "Do not run the batch again" in step.next_action


def test_failed_move_points_at_the_file_that_was_left_behind(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "locked.pdf", b"data")
    batch_id = history.create_batch(BATCH_ORGANIZE, workspace)
    history.record_operation(
        batch_id,
        OP_MOVE,
        source=str(source),
        destination=str(workspace / "Documents" / "locked.pdf"),
        size=4,
        status=STATUS_FAILED,
        error="Access denied",
    )

    (step,) = recovery_steps(batch_id, history)
    assert "Access denied" in step.detail
    assert str(source) in step.detail
    assert "run it again" in step.next_action


def test_failed_undo_says_where_the_file_still_is(workspace: Path, history: HistoryRepository) -> None:
    destination = make_file(workspace / "Documents" / "report.pdf", b"data")
    batch_id = history.create_batch(BATCH_ORGANIZE, workspace)
    operation_id = history.record_operation(
        batch_id,
        OP_MOVE,
        source=str(workspace / "report.pdf"),
        destination=str(destination),
        size=4,
        status=STATUS_SUCCESS,
    )
    history.set_undo_result(operation_id, UNDO_FAILED, "Access denied")

    (step,) = recovery_steps(batch_id, history)
    assert "Undo did not finish" in step.title
    assert str(destination) in step.detail
    assert "run Undo again" in step.next_action


def test_a_clean_batch_needs_no_guidance(
    workspace: Path, options: ScanOptions, classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "report.pdf")
    plan = build_plan(scan_folder(workspace, options), classifier)
    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 1

    assert recovery_steps(outcome.batch_id, history) == ()
    assert unfinished_batches(history) == []


def test_interrupted_batches_are_listed(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf", b"data")
    batch_id, _ = _interrupted_move(history, source, workspace / "Documents" / "report.pdf")

    batches = unfinished_batches(history)
    assert [batch.id for batch in batches] == [batch_id]
    assert batches[0].status == "interrupted"


def test_unknown_batch_is_reported(history: HistoryRepository) -> None:
    with pytest.raises(RecoveryError, match="no longer exists"):
        recovery_steps("missing", history)


def test_step_text_names_both_paths(workspace: Path, history: HistoryRepository) -> None:
    source = make_file(workspace / "report.pdf", b"data")
    destination = workspace / "Documents" / "report.pdf"
    batch_id, _ = _interrupted_move(history, source, destination)

    (step,) = recovery_steps(batch_id, history)
    assert step.source == str(source)
    assert step.destination == str(destination)
    assert step.text.startswith("This move was interrupted")
    assert "Next step:" in step.text
