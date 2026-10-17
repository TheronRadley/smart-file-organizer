import os
from pathlib import Path

import pytest

from smart_file_organizer.config import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    ORGANIZE_BY_DATE,
    ORGANIZE_BY_EXTENSION,
)
from smart_file_organizer.core import organizer
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_WOULD_MOVE,
    ScanOptions,
)
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import scan_folder

from .conftest import make_file


def _plan(root: Path, options: ScanOptions, classifier: Classifier, **kwargs):
    scan = scan_folder(root, options)
    return build_plan(scan, classifier, **kwargs)


def _sources(plan) -> dict[str, object]:
    return {op.source.name: op for op in plan.operations}


def test_example_from_specification(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "report.pdf")
    make_file(workspace / "vacation.jpg")
    make_file(workspace / "recording.mp3")
    make_file(workspace / "project.zip")
    plan = _plan(workspace, options, classifier)
    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 4
    assert (workspace / "Documents" / "report.pdf").is_file()
    assert (workspace / "Images" / "vacation.jpg").is_file()
    assert (workspace / "Audio" / "recording.mp3").is_file()
    assert (workspace / "Archives" / "project.zip").is_file()
    assert not (workspace / "report.pdf").exists()


def test_preview_does_not_change_filesystem(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "report.pdf")
    plan = _plan(workspace, options, classifier)
    outcome = execute_plan(plan, history=None, dry_run=True)
    assert outcome.batch_id is None
    assert outcome.count(STATUS_WOULD_MOVE) == 1
    assert (workspace / "report.pdf").exists()
    assert not (workspace / "Documents").exists()
    assert history.list_batches() == []


def test_selected_subset_only_moves_chosen_files(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf")
    make_file(workspace / "b.pdf")
    plan = _plan(workspace, options, classifier)
    first = next(op.index for op in plan.operations if op.source.name == "a.pdf")
    outcome = execute_plan(plan, history=history, selected=[first])
    assert outcome.success_count == 1
    assert (workspace / "Documents" / "a.pdf").exists()
    assert (workspace / "b.pdf").exists()


def test_collision_with_existing_file_is_renamed_never_overwritten(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "Documents" / "report.pdf", b"ORIGINAL")
    make_file(workspace / "report.pdf", b"incoming")
    plan = _plan(workspace, options, classifier, collision_strategy=COLLISION_RENAME)
    execute_plan(plan, history=history)
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"ORIGINAL"
    assert (workspace / "Documents" / "report (2).pdf").read_bytes() == b"incoming"


def test_collision_skip_strategy_leaves_file_in_place(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "Documents" / "report.pdf", b"ORIGINAL")
    make_file(workspace / "report.pdf", b"incoming")
    plan = _plan(workspace, options, classifier, collision_strategy=COLLISION_SKIP)
    (op,) = [op for op in plan.operations if op.source.name == "report.pdf"]
    assert op.status == STATUS_SKIPPED
    outcome = execute_plan(plan, history=history)
    assert outcome.skipped_count == 1
    assert (workspace / "report.pdf").read_bytes() == b"incoming"


def test_plan_resolves_collisions_between_batch_members(
    workspace: Path, recursive_options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "one" / "same.txt", b"1")
    make_file(workspace / "two" / "same.txt", b"2")
    plan = _plan(workspace, recursive_options, classifier)
    targets = {str(op.destination) for op in plan.ready}
    assert len(targets) == 2
    execute_plan(plan, history=history)
    assert sorted(p.read_bytes() for p in (workspace / "Documents").iterdir()) == [b"1", b"2"]


def test_organized_folders_are_never_reprocessed(
    workspace: Path, recursive_options: ScanOptions, classifier: Classifier
) -> None:
    make_file(workspace / "Documents" / "old" / "nested.pdf")
    make_file(workspace / "Documents" / "already.pdf")
    make_file(workspace / "loose.pdf")
    plan = _plan(workspace, recursive_options, classifier)
    reasons = {op.source.name: op.reason for op in plan.skipped}
    assert "nested.pdf" in reasons and "Documents" in reasons["nested.pdf"]
    assert "already.pdf" in reasons
    assert [op.source.name for op in plan.ready] == ["loose.pdf"]


def test_disabled_category_is_left_alone(workspace: Path, options: ScanOptions, classifier: Classifier) -> None:
    make_file(workspace / "song.mp3")
    make_file(workspace / "doc.pdf")
    plan = _plan(workspace, options, classifier, disabled_categories={"Audio"})
    by_name = _sources(plan)
    assert by_name["song.mp3"].status == STATUS_SKIPPED
    assert by_name["doc.pdf"].status == "ready"


def test_organize_by_extension_and_date(workspace: Path, options: ScanOptions, classifier: Classifier) -> None:
    make_file(workspace / "notes.txt", mtime=1_700_000_000)  # 2023-11
    make_file(workspace / "README", mtime=1_700_000_000)
    by_ext = _plan(workspace, options, classifier, organize_by=ORGANIZE_BY_EXTENSION)
    folders = {op.source.name: op.destination_folder for op in by_ext.ready}
    assert folders == {"notes.txt": "TXT", "README": "No Extension"}
    by_date = _plan(workspace, options, classifier, organize_by=ORGANIZE_BY_DATE)
    assert {op.destination_folder for op in by_date.ready} == {by_date.ready[0].destination_folder}
    assert by_date.ready[0].destination_folder[:2] == "20"


def test_plan_rejects_unknown_modes(workspace: Path, options: ScanOptions, classifier: Classifier) -> None:
    scan = scan_folder(workspace, options)
    with pytest.raises(ValueError):
        build_plan(scan, classifier, organize_by="colour")
    with pytest.raises(ValueError):
        build_plan(scan, classifier, collision_strategy="overwrite")


def test_unicode_filenames_move_intact(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "請求書 2026 é.pdf", b"ok")
    execute_plan(_plan(workspace, options, classifier), history=history)
    assert (workspace / "Documents" / "請求書 2026 é.pdf").read_bytes() == b"ok"


def test_source_changed_after_scan_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    source = make_file(workspace / "grow.pdf", b"short")
    plan = _plan(workspace, options, classifier)
    source.write_bytes(b"much longer content now")
    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert "changed" in outcome.outcomes[0].reason
    assert source.exists()


def test_source_replaced_by_different_file_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    source = make_file(workspace / "swap.pdf", b"AAAA", mtime=1_700_000_000)
    plan = _plan(workspace, options, classifier)
    assert plan.operations[0].file_id is not None
    replacement = make_file(workspace / "replacement.tmp", b"BBBB", mtime=1_700_000_000)
    os.replace(replacement, source)  # same size and timestamp, different file object
    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert "replaced by a different file" in outcome.outcomes[0].reason
    assert source.read_bytes() == b"BBBB"


def test_source_deleted_after_scan_is_reported(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    source = make_file(workspace / "gone.pdf")
    plan = _plan(workspace, options, classifier)
    source.unlink()
    outcome = execute_plan(plan, history=history)
    assert outcome.skipped_count == 1
    assert "no longer exists" in outcome.outcomes[0].reason


def test_destination_blocked_by_file_is_skipped(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "Documents", b"I am a file, not a folder")
    make_file(workspace / "report.pdf")
    plan = _plan(workspace, options, classifier)
    by_name = _sources(plan)
    # The blocker is itself a file, so it is organized into Other/ like any other extensionless file.
    assert by_name["Documents"].status == "ready"
    assert by_name["report.pdf"].status == STATUS_SKIPPED
    assert "in the way" in by_name["report.pdf"].reason


def test_partial_batch_reports_each_outcome(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_file(workspace / "a.pdf")
    make_file(workspace / "b.pdf")
    make_file(workspace / "c.pdf")
    plan = _plan(workspace, options, classifier)
    real_move = organizer.move_no_overwrite

    def flaky_move(source: Path, destination: Path, **kwargs) -> None:
        if source.name == "b.pdf":
            raise PermissionError(13, "Permission denied", str(source))
        real_move(source, destination, **kwargs)

    monkeypatch.setattr(organizer, "move_no_overwrite", flaky_move)
    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 2
    assert outcome.failed_count == 1
    assert (workspace / "b.pdf").exists()
    batch = history.list_batches()[0]
    assert batch.status == "partial"
    moves = [op for op in history.list_operations(batch.id) if op.op_type == "move"]
    statuses = {op.source_path.rsplit(os.sep, 1)[-1]: op.status for op in moves}
    assert statuses == {"a.pdf": STATUS_SUCCESS, "b.pdf": STATUS_FAILED, "c.pdf": STATUS_SUCCESS}


def test_cancellation_mid_batch_records_remaining_as_skipped(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    import threading

    make_file(workspace / "a.pdf")
    make_file(workspace / "b.pdf")
    plan = _plan(workspace, options, classifier)
    event = threading.Event()

    def stop_after_first(position: int, total: int) -> None:
        if position == 1:
            event.set()

    outcome = execute_plan(plan, history=history, cancel_event=event, progress=stop_after_first)
    assert outcome.cancelled
    assert outcome.success_count == 1
    assert outcome.skipped_count == 1
    assert history.list_batches()[0].status == "cancelled"


def test_folder_creation_is_recorded_for_undo(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / "a.pdf")
    execute_plan(_plan(workspace, options, classifier), history=history)
    batch = history.list_batches()[0]
    kinds = [op.op_type for op in history.list_operations(batch.id)]
    assert kinds == ["create_folder", "move"]


def test_hidden_files_are_not_moved_by_default(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository
) -> None:
    make_file(workspace / ".env")
    plan = _plan(workspace, options, classifier)
    assert plan.operations == ()


def test_move_no_overwrite_refuses_occupied_destination(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import errno

    from smart_file_organizer.core import fileops

    source = make_file(workspace / "source.txt", b"source-bytes")
    destination = make_file(workspace / "dest.txt", b"existing-bytes")
    with pytest.raises(FileExistsError):
        fileops.move_no_overwrite(source, destination)
    assert source.read_bytes() == b"source-bytes"
    assert destination.read_bytes() == b"existing-bytes"

    def no_hard_links(src: Path, dst: Path) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    monkeypatch.setattr(fileops.os, "link", no_hard_links)
    with pytest.raises(FileExistsError):
        fileops.move_no_overwrite(source, destination)
    assert source.read_bytes() == b"source-bytes"
    assert destination.read_bytes() == b"existing-bytes"


def test_move_no_overwrite_fails_closed_on_posix_without_hard_links(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import errno

    from smart_file_organizer.core import fileops

    source = make_file(workspace / "report.pdf", b"important-data")
    plan = _plan(workspace, options, classifier)

    def no_hard_links(src: Path, dst: Path) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    def forbidden_rename(src: Path, dst: Path) -> None:
        raise AssertionError("os.rename must not be called as a POSIX fallback")

    monkeypatch.setattr(fileops.os, "name", "posix")
    monkeypatch.setattr(fileops.os, "link", no_hard_links)
    monkeypatch.setattr(fileops.os, "rename", forbidden_rename)

    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert outcome.failed_count == 1
    assert "hard links unavailable" in outcome.outcomes[0].reason
    assert source.read_bytes() == b"important-data"
    assert not (workspace / "Documents" / "report.pdf").exists()


# ----- check/use races between the preview and the move --------------------


def test_source_replaced_by_a_link_outside_the_folder_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    from .conftest import symlinks_supported

    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside content")
    source = make_file(workspace / "report.pdf", b"original")
    plan = _plan(workspace, options, classifier)

    source.unlink()
    os.symlink(outside, source)  # the same name now points at a file elsewhere

    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert "outside the selected folder" in outcome.outcomes[0].reason
    assert os.path.islink(source), "the link must be left exactly as it was found"
    assert outside.read_bytes() == b"outside content"
    assert not (workspace / "Documents" / "report.pdf").exists()


def test_source_replaced_by_a_link_inside_the_folder_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    from .conftest import symlinks_supported

    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    source = make_file(workspace / "report.pdf", b"original")
    plan = _plan(workspace, options, classifier)
    other = make_file(workspace / "other.pdf", b"other content")  # added after the preview: not part of the batch

    source.unlink()
    os.symlink(other, source)  # inside the root, so only the regular-file check can catch it

    outcome = execute_plan(plan, history=history)
    report = next(o for o in outcome.outcomes if o.source.endswith("report.pdf"))
    assert report.status == STATUS_SKIPPED
    assert "not a regular file" in report.reason
    assert os.path.islink(source)
    assert other.read_bytes() == b"other content"
    assert not (workspace / "Documents" / "report.pdf").exists()


def test_destination_folder_replaced_by_a_link_is_refused(
    workspace: Path, options: ScanOptions, classifier: Classifier, history: HistoryRepository, tmp_path: Path
) -> None:
    from .conftest import symlinks_supported

    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside"
    outside.mkdir()
    make_file(workspace / "report.pdf", b"original")
    plan = _plan(workspace, options, classifier)
    os.symlink(outside, workspace / "Documents")

    outcome = execute_plan(plan, history=history)
    assert outcome.success_count == 0
    assert "blocked by a file or link" in outcome.outcomes[0].reason
    assert list(outside.iterdir()) == [], "nothing may be written through a link out of the folder"
    assert (workspace / "report.pdf").read_bytes() == b"original"


def test_source_swapped_while_the_destination_folder_is_created_is_refused(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_file(workspace / "report.pdf", b"original")
    plan = _plan(workspace, options, classifier)
    real_mkdir = os.mkdir

    def mkdir_then_swap(path, *args, **kwargs):
        result = real_mkdir(path, *args, **kwargs)
        # Another program replaces the source while the destination folder is created.
        source.unlink()
        make_file(source, b"a longer replacement")
        return result

    monkeypatch.setattr(organizer.os, "mkdir", mkdir_then_swap)
    outcome = execute_plan(plan, history=history)

    assert outcome.success_count == 0
    assert "after the preview" in outcome.outcomes[0].reason
    assert source.read_bytes() == b"a longer replacement", "the replacement file must not have been moved"
    assert not (workspace / "Documents" / "report.pdf").exists()


def test_destination_appearing_during_the_batch_is_not_overwritten(
    workspace: Path,
    options: ScanOptions,
    classifier: Classifier,
    history: HistoryRepository,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_file(workspace / "report.pdf", b"incoming")
    plan = _plan(workspace, options, classifier)
    real_move = organizer.move_no_overwrite

    def occupy_destination_then_move(source: Path, destination: Path, **kwargs) -> None:
        # Another program creates the target after every check but before the move.
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"written by another program")
        real_move(source, destination, **kwargs)

    monkeypatch.setattr(organizer, "move_no_overwrite", occupy_destination_then_move)
    outcome = execute_plan(plan, history=history)

    assert outcome.success_count == 0
    assert outcome.skipped_count == 1
    assert "another program" in outcome.outcomes[0].reason
    assert (workspace / "Documents" / "report.pdf").read_bytes() == b"written by another program"
    assert (workspace / "report.pdf").read_bytes() == b"incoming", "the source stays put; nothing is overwritten"
