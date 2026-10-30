"""Guarded reversal of completed organization batches.

Undo is a compensating operation, not a rollback: every move is checked before
anything is changed. A batch is reversed only when *every* outstanding move can
be reversed safely; otherwise the filesystem is left untouched and the reasons
are reported. Checks cover the moved file's identity, size and timestamp, and
whether the original location is free.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from dataclasses import dataclass, field
from pathlib import Path

from smart_file_organizer.core.fileops import MoveRollbackError, move_no_overwrite
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_ORGANIZE,
    BATCH_WATCH,
    OP_CREATE_FOLDER,
    OP_MOVE,
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
from smart_file_organizer.utils.paths import is_link_like, is_within

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


@dataclass(frozen=True)
class UndoPreview:
    batch: BatchRecord
    items: tuple[UndoItem, ...]
    folders_to_remove: tuple[OperationRecord, ...] = ()
    notes: tuple[str, ...] = field(default=())

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

    @property
    def fully_reversed(self) -> bool:
        return self.failed == 0 and self.skipped == 0


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
    operations = history.list_operations(batch_id)
    moves = [
        op for op in operations if op.op_type == OP_MOVE and op.status == STATUS_SUCCESS and op.undo_status != UNDO_DONE
    ]
    items = tuple(_check_move(op, root) for op in reversed(moves))
    folders = tuple(
        op
        for op in reversed(operations)
        if op.op_type == OP_CREATE_FOLDER and op.status == STATUS_SUCCESS and op.undo_status is None
    )
    notes: list[str] = []
    if not items:
        notes.append("Every move in this batch has already been reversed.")
    return UndoPreview(batch=batch, items=items, folders_to_remove=folders, notes=tuple(notes))


def _check_move(operation: OperationRecord, root: Path) -> UndoItem:
    original = Path(operation.source_path or "")
    current = Path(operation.destination_path or "")

    def blocked(reason: str) -> UndoItem:
        return UndoItem(operation, original, current, UNDO_STATUS_BLOCKED, reason)

    if not operation.source_path or not operation.destination_path:
        return blocked("History record is incomplete")
    if not is_within(original, root) or not is_within(current, root):
        return blocked("Recorded path is outside the batch folder")
    try:
        stats = os.lstat(current)
    except FileNotFoundError:
        return blocked("The file is no longer at its organized location (moved, renamed or deleted)")
    except OSError as exc:
        return blocked(f"The file cannot be read: {describe_os_error(exc)}")
    if not stat.S_ISREG(stats.st_mode):
        return blocked("The organized location is no longer a regular file")
    if operation.size_bytes is not None and stats.st_size != operation.size_bytes:
        return blocked("The file was modified after it was organized")
    if operation.modified_ns is not None and stats.st_mtime_ns != operation.modified_ns:
        return blocked("The file was modified after it was organized")
    if operation.file_id is not None and file_identity(stats, current) != operation.file_id:
        return blocked("The organized location now holds a different file")
    if os.path.lexists(original):
        return blocked("The original location is occupied by another file")
    parent = original.parent
    if os.path.lexists(parent) and (is_link_like(parent) or not parent.is_dir()):
        return blocked("The original folder is now a file or link")
    if os.path.lexists(parent) and not is_within(parent, root):
        return blocked("The original folder resolves outside the batch folder")
    return UndoItem(operation, original, current, UNDO_STATUS_READY)


def undo_batch(batch_id: str, history: HistoryRepository, *, cancel_event: threading.Event | None = None) -> UndoResult:
    """Reverse a batch if it is safe to do so. Refuses (changing nothing) otherwise.

    Individual moves are re-checked just before they run, so a file changed
    during the undo is reported instead of being overwritten. The batch root
    is locked against concurrent organize/watch operations for the duration.
    """
    preview_root = preview_undo(batch_id, history).batch.root_path
    with operation_lock(preview_root) as acquired:
        if not acquired:
            raise UndoError("Another file operation is already running for this folder; undo changed nothing.")
        return _undo_batch_locked(batch_id, history, cancel_event=cancel_event)


def _undo_batch_locked(
    batch_id: str, history: HistoryRepository, *, cancel_event: threading.Event | None = None
) -> UndoResult:
    preview = preview_undo(batch_id, history)
    if not preview.items:
        raise UndoError("Every move in this batch has already been reversed.")
    if preview.blocked:
        reasons = "; ".join(f"{item.current.name}: {item.reason}" for item in preview.blocked[:5])
        extra = len(preview.blocked) - 5
        suffix = f" (and {extra} more)" if extra > 0 else ""
        raise UndoError(
            f"Undo refused because {len(preview.blocked)} file(s) cannot be restored safely: {reasons}{suffix}. "
            "No files were changed."
        )

    restored = failed = skipped = 0
    messages: list[str] = []
    for item in preview.items:
        if cancel_event is not None and cancel_event.is_set():
            skipped += 1
            messages.append(f"Skipped {item.current.name}: Cancelled before this file was restored")
            continue
        current_check = _check_move(item.operation, Path(preview.batch.root_path))
        if current_check.status == UNDO_STATUS_BLOCKED:
            skipped += 1
            messages.append(f"Skipped {item.current.name}: {current_check.reason}")
            continue
        try:
            item.original.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failed += 1
            reason = describe_os_error(exc)
            history.set_undo_result(item.operation.id, UNDO_FAILED, reason)
            messages.append(f"Could not recreate the original folder for {item.current.name}: {reason}")
            continue

        # Recreating folders can take arbitrarily long, so the move is re-checked
        # immediately before it happens rather than relying on the earlier pass.
        final_check = _check_move(item.operation, Path(preview.batch.root_path))
        if final_check.status == UNDO_STATUS_BLOCKED:
            skipped += 1
            messages.append(f"Skipped {item.current.name}: {final_check.reason}")
            continue
        # Journal undo intent BEFORE restoring: if the outcome write below (or
        # the process) fails after the file is restored, the operation stays in
        # 'undoing' state and the batch is marked undo_partial, which Recovery
        # guidance treats as needing verification - never as completed.
        try:
            history.set_undo_result(item.operation.id, UNDO_PENDING)
        except Exception:
            failed += 1
            messages.append(f"Could not record undo intent for {item.current.name}; it was not restored")
            continue
        try:
            move_no_overwrite(item.current, item.original, expected_identity=item.operation.file_id)
        except OSError as exc:
            failed += 1
            reason = describe_os_error(exc)
            if isinstance(exc, MoveRollbackError):
                # Nothing was lost, but an extra copy was kept: say so, and how to resolve it.
                reason = f"{reason}. {exc.recovery}"
            history.set_undo_result(item.operation.id, UNDO_FAILED, reason)
            messages.append(f"Could not restore {item.current.name}: {reason}")
            continue
        restored += 1
        try:
            history.set_undo_result(item.operation.id, UNDO_DONE)
        except Exception:
            # Restored, but the record still says 'undoing'. Counted as restored
            # for the user, left unresolved for recovery (batch: undo_partial).
            logger.exception("Restored %s but could not record the undo outcome", item.current.name)
            messages.append(
                f"Restored {item.current.name}, but recording it failed; check Recovery guidance after restarting"
            )

    removed = kept = 0
    for folder_op in preview.folders_to_remove:
        folder = Path(folder_op.destination_path or "")
        try:
            if folder.is_dir() and not is_link_like(folder) and not any(folder.iterdir()):
                folder.rmdir()
                removed += 1
                history.set_undo_result(folder_op.id, UNDO_DONE)
            else:
                kept += 1
                history.set_undo_result(
                    folder_op.id, UNDO_KEPT, "Folder is not empty or no longer exists; left in place"
                )
        except OSError as exc:
            kept += 1
            history.set_undo_result(folder_op.id, UNDO_KEPT, describe_os_error(exc))
        except Exception:
            kept += 1
            logger.exception("Could not record the folder outcome during undo")

    remaining = _remaining_moves(batch_id, history)
    try:
        if failed == 0 and skipped == 0 and remaining == 0:
            history.set_batch_status(batch_id, "undone", f"Reversed {restored} move(s)")
        else:
            history.set_batch_status(
                batch_id,
                "undo_partial",
                f"Reversed {restored}; {failed} failed, {skipped} skipped. Run Undo again after fixing the cause.",
            )
    except Exception:
        # The filesystem work is done and reported in the result; a failed
        # status write must not turn a completed restore into a crash. The
        # 'undoing' journal rows remain for Recovery guidance.
        logger.exception("Could not record the final undo status for batch %s", batch_id)
    return UndoResult(batch_id, restored, failed, skipped, removed, kept, tuple(messages))


def _remaining_moves(batch_id: str, history: HistoryRepository) -> int:
    return sum(
        1
        for op in history.list_operations(batch_id)
        if op.op_type == OP_MOVE and op.status == STATUS_SUCCESS and op.undo_status != UNDO_DONE
    )
