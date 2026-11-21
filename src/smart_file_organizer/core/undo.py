"""Guarded reversal of completed organization batches.

Undo is a compensating operation, not a rollback. Preflight checks every
outstanding recorded move before any restore begins. The checks are repeated
at mutation time; errors, interference, or cancellation can interrupt a restore
partway through the batch.
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

from smart_file_organizer.core.boundary import (
    BoundaryError,
    bound_directory,
    directory_identity,
    is_reparse_entry,
    remove_recorded_directory,
    writable_parts,
)
from smart_file_organizer.core.cancellation import Cancellation
from smart_file_organizer.core.fileops import MoveCancelled, move_no_overwrite
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_ORGANIZE,
    BATCH_WATCH,
    OP_CREATE_FOLDER,
    OP_MOVE,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    UNDO_DONE,
    UNDO_FAILED,
    UNDO_KEPT,
    UNDO_PENDING,
    BatchRecord,
    OperationRecord,
)
from smart_file_organizer.core.operation_guard import operation_lock
from smart_file_organizer.core.scanner import describe_os_error, file_identity
from smart_file_organizer.utils.paths import protected_root_reason

logger = logging.getLogger(__name__)

UNDO_STATUS_READY = "ready"
UNDO_STATUS_BLOCKED = "blocked"
UNDOABLE_KINDS = (BATCH_ORGANIZE, BATCH_WATCH)


class UndoError(RuntimeError):
    """The batch cannot be undone at all (unknown, dry run, or already reversed)."""


@dataclass(frozen=True)
class UndoItem:
    operation: OperationRecord
    original: Path
    current: Path
    status: str  # UNDO_STATUS_READY or UNDO_STATUS_BLOCKED
    reason: str = ""
    current_parent_identity: str | None = None
    original_parent_identity: str | None = None


@dataclass(frozen=True)
class UndoPreview:
    batch: BatchRecord
    items: tuple[UndoItem, ...]
    folders_to_remove: tuple[OperationRecord, ...] = ()
    notes: tuple[str, ...] = field(default=())
    root_identity: str | None = None

    @property
    def blocked(self) -> tuple[UndoItem, ...]:
        return tuple(item for item in self.items if item.status == UNDO_STATUS_BLOCKED)

    @property
    def can_undo(self) -> bool:
        return bool(self.items) and not self.blocked


@dataclass(frozen=True)
class UndoResult:
    batch_id: str
    restored: int
    failed: int
    skipped: int
    removed_folders: int
    kept_folders: int
    messages: tuple[str, ...]
    needs_recovery: bool = False

    @property
    def fully_reversed(self) -> bool:
        return self.failed == 0 and self.skipped == 0 and not self.needs_recovery


def preview_undo(batch_id: str, history: HistoryRepository) -> UndoPreview:
    """Check every outstanding move of a batch without changing anything."""
    batch = history.get_batch(batch_id)
    if batch is None:
        raise UndoError("That batch no longer exists in history.")
    if batch.dry_run:
        raise UndoError("A dry run changes nothing, so there is nothing to undo.")
    if batch.kind not in UNDOABLE_KINDS:
        raise UndoError("This kind of batch cannot be undone.")

    root = Path(batch.root_path)
    try:
        if protected := protected_root_reason(root):
            raise BoundaryError(f"The batch folder is protected: {protected}", root)
        root_id = directory_identity(root)
        if batch.root_identity is not None and root_id != batch.root_identity:
            raise BoundaryError("The batch folder has been replaced or moved", root)
    except OSError as exc:
        raise UndoError(f"The batch folder could not be verified: {describe_os_error(exc)}") from exc
    operations = history.list_operations(batch_id)
    moves = [
        op for op in operations if op.op_type == OP_MOVE and op.status == STATUS_SUCCESS and op.undo_status != UNDO_DONE
    ]
    items = tuple(_check_move(op, root, root_id) for op in reversed(moves))
    folders = tuple(
        op
        for op in reversed(operations)
        if op.op_type == OP_CREATE_FOLDER and op.status == STATUS_SUCCESS and op.undo_status is None
    )
    notes: list[str] = []
    if not items:
        if batch.needs_recovery:
            notes.append("No recorded moves can be undone; check Recovery guidance.")
        elif any(op.op_type == OP_MOVE and op.status == STATUS_SUCCESS for op in operations):
            notes.append("Every move in this batch has already been reversed.")
        else:
            notes.append("No recorded moves in this batch can be undone.")
    return UndoPreview(batch=batch, items=items, folders_to_remove=folders, notes=tuple(notes), root_identity=root_id)


def _check_move(operation: OperationRecord, root: Path, root_id: str) -> UndoItem:
    original = Path(operation.source_path or "")
    current = Path(operation.destination_path or "")

    def blocked(reason: str) -> UndoItem:
        return UndoItem(operation, original, current, UNDO_STATUS_BLOCKED, reason)

    if not operation.source_path or not operation.destination_path:
        return blocked("History record is incomplete")
    try:
        original_parts = writable_parts(original, root)
        writable_parts(current, root)
    except BoundaryError as exc:
        if "outside" in str(exc.strerror):
            return blocked("Recorded path is outside the batch folder")
        return blocked(describe_os_error(exc))
    try:
        with bound_directory(current.parent, root, root_identity=root_id) as directory:
            current_parent_id = directory.identity
            stats = directory.stat_entry(current.name)
    except FileNotFoundError:
        return blocked("The file is no longer at its organized location (moved, renamed or deleted)")
    except OSError as exc:
        return blocked(f"The file cannot be read: {describe_os_error(exc)}")
    if not stat.S_ISREG(stats.st_mode) or is_reparse_entry(stats):
        return blocked("The organized location is no longer a regular file")
    if operation.size_bytes is not None and stats.st_size != operation.size_bytes:
        return blocked("The file was modified after it was organized")
    if operation.modified_ns is not None and stats.st_mtime_ns != operation.modified_ns:
        return blocked("The file was modified after it was organized")
    if operation.file_id is not None and file_identity(stats, current) != operation.file_id:
        return blocked("The organized location now holds a different file")
    if os.path.lexists(original):
        return blocked("The original location is occupied by another file")
    if not original_parts:
        return blocked("The original location cannot be the batch folder")
    try:
        with bound_directory(original.parent, root, root_identity=root_id) as directory:
            original_parent_id = directory.identity
    except FileNotFoundError:
        original_parent_id = None  # Safely recreated only inside the validated root.
    except OSError as exc:
        return blocked(f"Original folder could not be verified: {describe_os_error(exc)}")
    return UndoItem(
        operation,
        original,
        current,
        UNDO_STATUS_READY,
        current_parent_identity=current_parent_id,
        original_parent_identity=original_parent_id,
    )


def undo_batch(batch_id: str, history: HistoryRepository, *, cancel_event: Cancellation | None = None) -> UndoResult:
    """Reverse recorded moves after a read-only all-or-nothing preflight.

    Individual moves are re-checked just before they run, so a file changed
    during the undo is reported instead of being overwritten. The batch root
    is locked against concurrent organize/watch operations for the duration.
    """
    batch = history.get_batch(batch_id)
    if batch is None:
        raise UndoError("That batch no longer exists in history.")
    preview_root = batch.root_path
    with operation_lock(preview_root) as acquired:
        if not acquired:
            raise UndoError("Another file operation is already running for this folder; undo changed nothing.")
        return _undo_batch_locked(batch_id, history, cancel_event=cancel_event)


def _undo_batch_locked(
    batch_id: str, history: HistoryRepository, *, cancel_event: Cancellation | None = None
) -> UndoResult:
    preview = preview_undo(batch_id, history)
    if not preview.items:
        raise UndoError(preview.notes[0])
    if preview.blocked:
        reasons = "; ".join(f"{item.current.name}: {item.reason}" for item in preview.blocked[:5])
        extra = len(preview.blocked) - 5
        suffix = f" (and {extra} more)" if extra > 0 else ""
        raise UndoError(
            f"Undo refused because {len(preview.blocked)} file(s) cannot be restored safely: {reasons}{suffix}. "
            "No files were changed."
        )

    root = Path(preview.batch.root_path)
    root_id = preview.root_identity
    assert root_id is not None
    restored = failed = skipped = 0
    messages: list[str] = []
    unresolved = False
    for item in preview.items:
        if cancel_event is not None and cancel_event.is_set():
            skipped += 1
            messages.append(f"Skipped {item.current.name}: Cancelled before this file was restored")
            continue
        current_check = _check_move(item.operation, root, root_id)
        if current_check.status == UNDO_STATUS_BLOCKED:
            skipped += 1
            messages.append(f"Skipped {item.current.name}: {current_check.reason}")
            continue
        try:
            history.set_undo_result(item.operation.id, UNDO_PENDING)
        except Exception:
            failed += 1
            logger.exception("Could not record undo intent for %s", item.current.name)
            messages.append(f"Could not record undo intent for {item.current.name}; it was not restored")
            continue
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise MoveCancelled(item.current)
            original_parent_id = current_check.original_parent_identity
            if original_parent_id is None:
                with bound_directory(item.original.parent, root, root_identity=root_id, create=True) as directory:
                    original_parent_id = directory.identity
            move_no_overwrite(
                item.current,
                item.original,
                expected_identity=item.operation.file_id,
                expected_size=item.operation.size_bytes,
                expected_modified_ns=item.operation.modified_ns,
                root=root,
                root_identity=root_id,
                source_parent_identity=current_check.current_parent_identity,
                destination_parent_identity=original_parent_id,
                cancel_event=cancel_event,
            )
        except MoveCancelled:
            skipped += 1
            reason = "Cancelled before this file was restored"
            unresolved |= not _record_undo(history, item.operation.id, UNDO_FAILED, reason)
            messages.append(f"Skipped {item.current.name}: {reason}")
            continue
        except OSError as exc:
            failed += 1
            reason = describe_os_error(exc)
            unresolved |= not _record_undo(history, item.operation.id, UNDO_FAILED, reason)
            messages.append(f"Could not restore {item.current.name}: {reason}")
            continue
        restored += 1
        if not _record_undo(history, item.operation.id, UNDO_DONE):
            unresolved = True
            messages.append(f"Restored {item.current.name}, but recording it failed; check Recovery guidance")

    removed = kept = 0
    for folder_op in preview.folders_to_remove:
        if cancel_event is not None and cancel_event.is_set():
            break
        folder = Path(folder_op.destination_path or "")
        try:
            remove_recorded_directory(folder, root, root_identity=root_id, directory_id=folder_op.file_id)
        except OSError as exc:
            kept += 1
            _record_undo(history, folder_op.id, UNDO_KEPT, describe_os_error(exc))
        else:
            removed += 1
            _record_undo(history, folder_op.id, UNDO_DONE)

    try:
        remaining = _remaining_moves(batch_id, history)
        unresolved |= any(op.status in (STATUS_PENDING, STATUS_INTERRUPTED) for op in remaining)
        if failed == 0 and skipped == 0 and not remaining and not unresolved:
            history.set_batch_status(batch_id, "undone", f"Reversed {restored} move(s)")
        else:
            next_step = "Check Recovery guidance." if unresolved else "Run Undo again after fixing the cause."
            history.set_batch_status(
                batch_id, "undo_partial", f"Reversed {restored}; {failed} failed, {skipped} skipped. {next_step}"
            )
    except Exception:
        unresolved = True
        logger.exception("Could not record the final undo status for batch %s", batch_id)
        messages.append("Could not update the batch status; check Recovery guidance in History.")
    return UndoResult(batch_id, restored, failed, skipped, removed, kept, tuple(messages), unresolved)


def _record_undo(history: HistoryRepository, operation_id: int, status: str, reason: str | None = None) -> bool:
    try:
        history.set_undo_result(operation_id, status, reason)
    except Exception:
        logger.exception("Could not record undo outcome for operation %d", operation_id)
        return False
    return True


def _remaining_moves(batch_id: str, history: HistoryRepository) -> list[OperationRecord]:
    return [
        op
        for op in history.list_operations(batch_id)
        if op.op_type == OP_MOVE
        and (
            op.status in (STATUS_PENDING, STATUS_INTERRUPTED)
            or (op.status == STATUS_SUCCESS and op.undo_status != UNDO_DONE)
        )
    ]
