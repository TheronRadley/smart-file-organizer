"""Organization planning and guarded execution. Independent of the user interface.

Workflow:

1. :func:`build_plan` turns a :class:`ScanResult` into proposed moves, skipping
   anything unsafe or ambiguous and reserving destination names so that files
   in the same batch never collide with each other.
2. :func:`execute_plan` re-validates every selected operation immediately
   before touching the filesystem (source identity, size and timestamp,
   destination containment, collisions), records it in history, and moves it
   with :func:`~smart_file_organizer.core.fileops.move_no_overwrite`.

Each file is moved individually. The filesystem cannot provide a multi-file
transaction, so a batch can end partially applied; outcomes are recorded
or left pending for recovery if its outcome could not be written.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable, Collection, Iterable
from datetime import datetime
from pathlib import Path

from smart_file_organizer.config import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    COLLISION_STRATEGIES,
    ORGANIZE_BY_DATE,
    ORGANIZE_BY_EXTENSION,
    ORGANIZE_BY_TYPE,
    ORGANIZE_MODES,
)
from smart_file_organizer.core.boundary import (
    BoundaryError,
    bound_directory,
    directory_identity,
    is_reparse_entry,
    writable_parts,
)
from smart_file_organizer.core.cancellation import Cancellation
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.fileops import MoveCancelled, SourceChangedError, move_no_overwrite
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    BATCH_ORGANIZE,
    OP_CREATE_FOLDER,
    OP_MOVE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_READY,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_WOULD_MOVE,
    BatchOutcome,
    FileEntry,
    OperationOutcome,
    OrganizePlan,
    PlannedOperation,
    ScanResult,
)
from smart_file_organizer.core.operation_guard import operation_lock
from smart_file_organizer.core.scanner import describe_os_error, file_identity
from smart_file_organizer.utils.paths import (
    is_link_like,
    sanitize_name,
    validate_name,
)

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]
DATE_FOLDER_FORMAT = "%Y-%m"
NO_EXTENSION_FOLDER = "No Extension"
MAX_COLLISION_NUMBER = 10_000
CANCELLED_REASON = "Cancelled before this file was processed"
BUSY_REASON = "Another file operation is already running for this folder; nothing was moved"
DESTINATION_RACE_REASON = "Destination was created by another program during the batch; skipped"
DESTINATION_CHANGED_REASON = "The approved destination is occupied; rebuild the preview before moving this file"


def destination_folder_for(entry: FileEntry, organize_by: str, classifier: Classifier) -> str:
    """Return the destination folder name (relative to the root) for ``entry``."""
    if organize_by == ORGANIZE_BY_TYPE:
        return classifier.folder_for_category(entry.category)
    if organize_by == ORGANIZE_BY_EXTENSION:
        return sanitize_name(entry.extension.upper()) if entry.extension else NO_EXTENSION_FOLDER
    if organize_by == ORGANIZE_BY_DATE:
        return datetime.fromtimestamp(entry.modified_ns / 1_000_000_000).strftime(DATE_FOLDER_FORMAT)
    raise ValueError(f"Unknown organize mode: {organize_by}")


def build_plan(
    scan: ScanResult,
    classifier: Classifier,
    *,
    organize_by: str = ORGANIZE_BY_TYPE,
    collision_strategy: str = COLLISION_RENAME,
    disabled_categories: Collection[str] = (),
) -> OrganizePlan:
    """Propose a move for every file in ``scan`` without touching the filesystem."""
    if organize_by not in ORGANIZE_MODES:
        raise ValueError(f"Unknown organize mode: {organize_by}")
    if collision_strategy not in COLLISION_STRATEGIES:
        raise ValueError(f"Unknown collision strategy: {collision_strategy}")

    disabled = set(disabled_categories)
    folders = [destination_folder_for(entry, organize_by, classifier) for entry in scan.entries]
    # Files already inside any folder this tool manages are never organized again.
    protected = classifier.managed_folder_names() | {folder.casefold() for folder in folders}

    reserved: set[str] = set()
    next_suffix: dict[str, int] = {}
    blocked_folders: dict[str, bool] = {}  # folder name -> True when a file or link is in the way
    operations: list[PlannedOperation] = []
    for index, (entry, folder) in enumerate(zip(scan.entries, folders, strict=True)):
        operations.append(
            _plan_entry(
                index,
                entry,
                folder,
                scan.root,
                collision_strategy=collision_strategy,
                protected=protected,
                reserved=reserved,
                skip_categories=disabled,
                blocked_folders=blocked_folders,
                next_suffix=next_suffix,
            )
        )
    return OrganizePlan(
        root=scan.root,
        organize_by=organize_by,
        collision_strategy=collision_strategy,
        operations=tuple(operations),
        root_identity=scan.root_identity,
    )


def _plan_entry(
    index: int,
    entry: FileEntry,
    folder: str,
    root: Path,
    *,
    collision_strategy: str,
    protected: set[str],
    reserved: set[str],
    skip_categories: set[str],
    blocked_folders: dict[str, bool],
    next_suffix: dict[str, int],
) -> PlannedOperation:
    def skipped(reason: str) -> PlannedOperation:
        return _operation(index, entry, folder="", destination=None, status=STATUS_SKIPPED, reason=reason)

    if entry.category in skip_categories:
        return skipped(f"The '{entry.category}' category is turned off in Settings")
    if entry.top_folder is not None and entry.top_folder.casefold() in protected:
        return skipped(f"Already inside the '{entry.top_folder}' folder; organized folders are not re-sorted")

    destination_dir = root / folder
    if folder not in blocked_folders:
        blocked_folders[folder] = is_link_like(destination_dir) or (
            os.path.lexists(destination_dir) and not destination_dir.is_dir()
        )
    if blocked_folders[folder]:
        return skipped(f"A file or link named '{folder}' is in the way of the destination folder")
    if _key(entry.path.parent) == _key(destination_dir):
        return skipped("Already in its destination folder")

    chosen = _choose_destination(destination_dir / entry.name, reserved, collision_strategy, next_suffix)
    if chosen is None:
        return skipped(f"A file named '{entry.name}' already exists in '{folder}' (collision strategy: skip)")
    reserved.add(_key(chosen))
    return _operation(index, entry, folder=folder, destination=chosen, status=STATUS_READY, reason="")


def _operation(
    index: int,
    entry: FileEntry,
    *,
    folder: str,
    destination: Path | None,
    status: str,
    reason: str,
) -> PlannedOperation:
    return PlannedOperation(
        index=index,
        source=entry.path,
        source_relative=entry.relative_path,
        size=entry.size,
        modified_ns=entry.modified_ns,
        file_id=entry.file_id,
        category=entry.category,
        destination_folder=folder,
        destination=destination,
        status=status,
        reason=reason,
    )


def _choose_destination(
    target: Path,
    reserved: set[str],
    strategy: str,
    next_suffix: dict[str, int],
) -> Path | None:
    """Return a free destination for ``target`` or None when the strategy forbids renaming.

    ``next_suffix`` remembers where the numbering for each base name stopped, so many files
    with the same name are numbered in linear rather than quadratic time.
    """
    if _is_free(target, reserved):
        return target
    if strategy == COLLISION_SKIP:
        return None
    base = _key(target)
    for number in range(next_suffix.get(base, 2), MAX_COLLISION_NUMBER):
        candidate = target.with_name(f"{target.stem} ({number}){target.suffix}")
        if _is_free(candidate, reserved):
            next_suffix[base] = number + 1
            return candidate
    return None


def _is_free(path: Path, reserved: set[str]) -> bool:
    return _key(path) not in reserved and not os.path.lexists(path)


def _key(path: Path) -> str:
    """Cheap comparison key for planning. Symlinks are not resolved here; execution re-checks them."""
    return os.path.normcase(os.path.abspath(path))


def check_source(operation: PlannedOperation, root: Path) -> str | None:
    """Re-validate a planned source against the filesystem. Returns a refusal reason or None.

    Read-only; used both for dry runs and immediately before each real move.
    """
    try:
        writable_parts(operation.source, root)
        stats = os.lstat(operation.source)
    except BoundaryError as exc:
        return str(exc.strerror)
    except FileNotFoundError:
        return "Source no longer exists"
    except OSError as exc:
        return f"Source cannot be read: {describe_os_error(exc)}"
    if not stat.S_ISREG(stats.st_mode) or is_reparse_entry(stats):
        return "Source is not a regular file (link or special file)"
    if stats.st_size != operation.size or stats.st_mtime_ns != operation.modified_ns:
        return "Source changed after the preview was built; rescan before organizing"
    if operation.file_id is not None and file_identity(stats, operation.source) != operation.file_id:
        return "Source was replaced by a different file after the preview was built"
    return None


def execute_plan(
    plan: OrganizePlan,
    *,
    history: HistoryRepository | None,
    selected: Iterable[int] | None = None,
    dry_run: bool = False,
    cancel_event: Cancellation | None = None,
    progress: ProgressCallback | None = None,
    kind: str = BATCH_ORGANIZE,
    exact_destinations: bool = True,
) -> BatchOutcome:
    """Apply (or dry-run) the selected operations of ``plan``.

    ``selected`` holds operation indices; None means every operation. Dry runs
    validate the preview but never create folders, move files or write history.
    Disable ``exact_destinations`` only for unattended, explicitly opted-in moves.
    """
    if not dry_run and history is None:
        raise ValueError("A history repository is required when changes are applied")
    chosen_indices = None if selected is None else set(selected)
    operations = [op for op in plan.operations if chosen_indices is None or op.index in chosen_indices]

    if dry_run:
        return _execute_plan(
            plan,
            operations,
            history=history,
            dry_run=True,
            cancel_event=cancel_event,
            progress=progress,
            kind=kind,
            exact_destinations=exact_destinations,
        )

    with operation_lock(plan.root) as acquired:
        if not acquired:
            # Another organize/undo/watch operation is moving files in this
            # folder right now. Acting on this preview could mean acting on a
            # half-moved tree, so every selected operation is refused.
            outcome = BatchOutcome(root=plan.root, dry_run=False, batch_id=None)
            outcome.outcomes.extend(
                OperationOutcome(
                    op.index,
                    str(op.source),
                    str(op.destination) if op.destination else None,
                    STATUS_SKIPPED,
                    BUSY_REASON,
                    op.size,
                )
                for op in operations
            )
            return outcome
        return _execute_plan(
            plan,
            operations,
            history=history,
            dry_run=False,
            cancel_event=cancel_event,
            progress=progress,
            kind=kind,
            exact_destinations=exact_destinations,
        )


def _execute_plan(
    plan: OrganizePlan,
    operations: list[PlannedOperation],
    *,
    history: HistoryRepository | None,
    dry_run: bool,
    cancel_event: Cancellation | None,
    progress: ProgressCallback | None,
    kind: str,
    exact_destinations: bool,
) -> BatchOutcome:
    batch_id: str | None = None
    if not dry_run and operations and history is not None:
        batch_id = history.create_batch(kind, plan.root, root_identity=plan.root_identity)
    outcome = BatchOutcome(root=plan.root, dry_run=dry_run, batch_id=batch_id)

    total = len(operations)
    for position, operation in enumerate(operations, start=1):
        if cancel_event is not None and cancel_event.is_set():
            outcome.cancelled = True
            for remaining in operations[position - 1 :]:
                outcome.outcomes.append(_record_skip(remaining, CANCELLED_REASON, history, batch_id))
            break
        result = _apply_operation(operation, plan, dry_run, history, batch_id, exact_destinations, cancel_event)
        outcome.outcomes.append(result)
        if result.reason == CANCELLED_REASON:
            outcome.cancelled = True
        if progress is not None:
            progress(position, total)

    if batch_id is not None and history is not None:
        try:
            history.finish_batch(batch_id, _batch_status(outcome), _summary(outcome))
        except Exception:
            logger.exception("Could not finalize batch %s in history; outcomes are still reported", batch_id)
    return outcome


def _batch_status(outcome: BatchOutcome) -> str:
    if outcome.needs_recovery:
        return "interrupted"
    if outcome.cancelled:
        return "cancelled"
    if outcome.failed_count:
        return "partial"
    return "completed"


def _summary(outcome: BatchOutcome) -> str:
    summary = f"{outcome.success_count} moved, {outcome.skipped_count} skipped, {outcome.failed_count} failed"
    return f"{summary}. Check Recovery guidance." if outcome.needs_recovery else summary


def _record_skip(
    operation: PlannedOperation,
    reason: str,
    history: HistoryRepository | None,
    batch_id: str | None,
) -> OperationOutcome:
    destination = str(operation.destination) if operation.destination else None
    if history is not None and batch_id is not None:
        history.record_operation(
            batch_id,
            OP_MOVE,
            source=str(operation.source),
            destination=destination,
            size=operation.size,
            modified_ns=operation.modified_ns,
            file_id=operation.file_id,
            status=STATUS_SKIPPED,
            error=reason,
        )
    return OperationOutcome(operation.index, str(operation.source), destination, STATUS_SKIPPED, reason, operation.size)


def _apply_operation(
    operation: PlannedOperation,
    plan: OrganizePlan,
    dry_run: bool,
    history: HistoryRepository | None,
    batch_id: str | None,
    exact_destinations: bool,
    cancel_event: Cancellation | None,
) -> OperationOutcome:
    if operation.status != STATUS_READY or operation.destination is None:
        return _record_skip(operation, operation.reason or "Not eligible", history, batch_id)

    if refusal := check_source(operation, plan.root):
        return _record_skip(operation, refusal, history, batch_id)
    target = operation.destination
    destination_dir = plan.root / operation.destination_folder
    if not operation.destination_folder or target.parent != destination_dir or not target.name:
        return _record_skip(operation, "Destination does not match the preview's folder", history, batch_id)
    try:
        if len(destination_dir.relative_to(plan.root).parts) != 1:
            raise BoundaryError("Destination must be directly in the selected folder", destination_dir)
        validate_name(destination_dir.name)
        root_id = directory_identity(plan.root)
        if plan.root_identity is not None and root_id != plan.root_identity:
            raise BoundaryError("The selected folder changed; choose it and scan again", plan.root)
        destination_id = directory_identity(destination_dir) if os.path.lexists(destination_dir) else None
        writable_parts(target, plan.root)
        if os.path.lexists(target):
            if exact_destinations or plan.collision_strategy == COLLISION_SKIP:
                return _record_skip(operation, DESTINATION_CHANGED_REASON, history, batch_id)
            target = _next_free_name(target)
    except (OSError, ValueError) as exc:
        reason = describe_os_error(exc) if isinstance(exc, OSError) else str(exc)
        return _record_skip(operation, reason, history, batch_id)

    if dry_run:
        return OperationOutcome(
            operation.index, str(operation.source), str(target), STATUS_WOULD_MOVE, "", operation.size
        )

    assert history is not None and batch_id is not None
    if cancel_event is not None and cancel_event.is_set():
        return _record_skip(operation, CANCELLED_REASON, history, batch_id)
    try:
        if destination_id is None:
            with bound_directory(plan.root, plan.root, root_identity=root_id) as parent:
                parent.recheck()
                try:
                    parent.mkdir(destination_dir.name)
                except FileExistsError:
                    created_here = False
                else:
                    created_here = True
                with bound_directory(destination_dir, plan.root, root_identity=root_id) as directory:
                    destination_id = directory.identity
                if created_here:
                    history.record_operation(
                        batch_id,
                        OP_CREATE_FOLDER,
                        source=None,
                        destination=str(destination_dir),
                        file_id=destination_id,
                        status=STATUS_SUCCESS,
                    )
        with bound_directory(operation.source.parent, plan.root, root_identity=root_id) as source_parent:
            source_parent_id = source_parent.identity
    except OSError as exc:
        return _record_skip(operation, f"Could not verify folders: {describe_os_error(exc)}", history, batch_id)

    operation_id = history.record_operation(
        batch_id,
        OP_MOVE,
        source=str(operation.source),
        destination=str(target),
        size=operation.size,
        modified_ns=operation.modified_ns,
        file_id=operation.file_id,
        status=STATUS_PENDING,
    )

    try:
        if cancel_event is not None and cancel_event.is_set():
            return _write_move_result(history, operation_id, operation, target, STATUS_SKIPPED, CANCELLED_REASON)
        move_no_overwrite(
            operation.source,
            target,
            expected_identity=operation.file_id,
            expected_size=operation.size,
            expected_modified_ns=operation.modified_ns,
            root=plan.root,
            root_identity=root_id,
            source_parent_identity=source_parent_id,
            destination_parent_identity=destination_id,
            cancel_event=cancel_event,
        )
    except MoveCancelled:
        return _write_move_result(history, operation_id, operation, target, STATUS_SKIPPED, CANCELLED_REASON)
    except (BoundaryError, SourceChangedError) as exc:
        return _write_move_result(history, operation_id, operation, target, STATUS_SKIPPED, describe_os_error(exc))
    except FileExistsError:
        return _write_move_result(history, operation_id, operation, target, STATUS_SKIPPED, DESTINATION_RACE_REASON)
    except OSError as exc:
        logger.warning("Move of %s failed: %s", operation.source.name, exc)
        return _write_move_result(history, operation_id, operation, target, STATUS_FAILED, describe_os_error(exc))
    return _write_move_result(history, operation_id, operation, target, STATUS_SUCCESS)


def _write_move_result(
    history: HistoryRepository,
    operation_id: int,
    operation: PlannedOperation,
    target: Path,
    status: str,
    reason: str = "",
) -> OperationOutcome:
    try:
        history.update_operation(operation_id, status, destination=str(target), error=reason or None)
    except Exception:
        logger.exception("Could not record the move outcome for %s", operation.source.name)
        warning = "Could not record this outcome; check Recovery guidance in History"
        return OperationOutcome(
            operation.index,
            str(operation.source),
            str(target),
            status,
            f"{reason}. {warning}" if reason else warning,
            operation.size,
            history_recorded=False,
        )
    return OperationOutcome(operation.index, str(operation.source), str(target), status, reason, operation.size)


def _next_free_name(target: Path) -> Path:
    for number in range(2, MAX_COLLISION_NUMBER):
        candidate = target.with_name(f"{target.stem} ({number}){target.suffix}")
        if not os.path.lexists(candidate):
            return candidate
    raise FileExistsError(f"No free name available for {target.name}")
