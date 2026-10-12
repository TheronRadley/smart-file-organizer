import os
from pathlib import Path

import pytest

from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import scan_folder
from smart_file_organizer.core.undo import UndoError, preview_undo, undo_batch

from .conftest import make_file


def _organize(workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository) -> str:
    plan = build_plan(scan_folder(workspace, options), classifier)
    outcome = execute_plan(plan, history=history)
    assert outcome.batch_id is not None
    return outcome.batch_id


def test_undo_restores_files_and_removes_created_folders(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    original = make_file(workspace / "report.pdf", b"contents")
    batch_id = _organize(workspace, options, classifier, history)
    result = undo_batch(batch_id, history)
    assert result.restored == 1
    assert result.fully_reversed
    assert result.removed_folders == 1
    assert original.read_bytes() == b"contents"
    assert not (workspace / "Documents").exists()
    assert history.get_batch(batch_id).status == "undone"


def test_undo_refuses_when_original_path_is_occupied(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "report.pdf", b"moved content")
    batch_id = _organize(workspace, options, classifier, history)
    make_file(workspace / "report.pdf", b"someone else's file")
    with pytest.raises(UndoError, match="occupied"):
        undo_batch(batch_id, history)
    assert (workspace / "report.pdf").read_bytes() == b"someone else's file"
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"moved content"


def test_undo_is_all_or_nothing_when_one_item_is_blocked(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf", b"a")
    make_file(workspace / "b.pdf", b"b")
    batch_id = _organize(workspace, options, classifier, history)
    (workspace / "a.pdf").write_bytes(b"blocker")  # original of a.pdf is now occupied
    with pytest.raises(UndoError):
        undo_batch(batch_id, history)
    assert (workspace / "Documents" / "b.pdf").exists(), "unblocked files must not be moved either"
    assert not (workspace / "b.pdf").exists()


def test_undo_refuses_when_organized_file_was_modified(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "notes.txt", b"v1")
    batch_id = _organize(workspace, options, classifier, history)
    (workspace / "Documents" / "notes.txt").write_bytes(b"v2 edited")
    preview = preview_undo(batch_id, history)
    assert not preview.can_undo
    assert "modified" in preview.blocked[0].reason
    with pytest.raises(UndoError):
        undo_batch(batch_id, history)
    assert (workspace / "Documents" / "notes.txt").read_bytes() == b"v2 edited"


def test_undo_refuses_when_destination_was_replaced(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "swap.pdf", b"AAAA", mtime=1_700_000_000)
    batch_id = _organize(workspace, options, classifier, history)
    replacement = make_file(workspace / "new.tmp", b"BBBB", mtime=1_700_000_000)
    os.replace(replacement, workspace / "Documents" / "swap.pdf")
    preview = preview_undo(batch_id, history)
    assert preview.blocked and "different file" in preview.blocked[0].reason


def test_undo_reports_missing_destination(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "gone.pdf")
    batch_id = _organize(workspace, options, classifier, history)
    (workspace / "Documents" / "gone.pdf").unlink()
    with pytest.raises(UndoError, match="no longer"):
        undo_batch(batch_id, history)


def test_undo_twice_reports_already_reversed(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf")
    batch_id = _organize(workspace, options, classifier, history)
    undo_batch(batch_id, history)
    with pytest.raises(UndoError, match="already been reversed"):
        undo_batch(batch_id, history)


def test_dry_run_batches_cannot_be_undone(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf")
    plan = build_plan(scan_folder(workspace, options), classifier)
    execute_plan(plan, history=None, dry_run=True)
    batch_id = history.create_batch("organize", workspace, dry_run=True)
    with pytest.raises(UndoError, match="dry run"):
        undo_batch(batch_id, history)


def test_unknown_batch_is_reported(history: HistoryRepository) -> None:
    with pytest.raises(UndoError, match="no longer exists"):
        undo_batch("does-not-exist", history)


def test_partial_undo_can_be_completed_later(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smart_file_organizer.core import undo as undo_module

    make_file(workspace / "a.pdf")
    make_file(workspace / "b.pdf")
    batch_id = _organize(workspace, options, classifier, history)

    real_move = undo_module.move_no_overwrite

    def fail_for_b(source: Path, destination: Path) -> None:
        if source.name == "b.pdf":
            raise PermissionError(13, "Permission denied", str(source))
        real_move(source, destination)

    monkeypatch.setattr(undo_module, "move_no_overwrite", fail_for_b)
    result = undo_batch(batch_id, history)
    assert result.restored == 1
    assert result.failed == 1
    assert history.get_batch(batch_id).status == "undo_partial"
    assert (workspace / "Documents" / "b.pdf").exists()

    monkeypatch.setattr(undo_module, "move_no_overwrite", real_move)
    second = undo_batch(batch_id, history)
    assert second.restored == 1
    assert (workspace / "b.pdf").exists()
    assert history.get_batch(batch_id).status == "undone"


def test_folder_with_new_user_files_is_kept(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf")
    batch_id = _organize(workspace, options, classifier, history)
    make_file(workspace / "Documents" / "added-later.txt")
    # The created folder still holds the moved file, so undo must refuse to delete it implicitly.
    result = undo_batch(batch_id, history)
    assert result.restored == 1
    assert result.kept_folders == 1
    assert (workspace / "Documents" / "added-later.txt").exists()
