"""Plain-language guidance for operations that did not finish cleanly.

Files are moved one at a time and the filesystem offers no multi-file
transaction, so a batch can end partly applied: the application was closed
during a move, a file was locked, or a destination turned out to be occupied.
History records the original and the new location of every move, so the safest
next step can be stated per file instead of leaving the user to guess.

Everything here is read-only. A path existing on disk is not proof that it
still holds the recorded file. Guidance distinguishes verified, missing and
unverified paths and does not tell users to delete uncertain entries.
"""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path

from smart_file_organizer.core.boundary import bound_directory, is_reparse_entry, writable_parts
from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    OP_MOVE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    UNDO_FAILED,
    UNDO_PENDING,
    BatchRecord,
    OperationRecord,
)
from smart_file_organizer.core.scanner import file_identity

logger = logging.getLogger(__name__)

SEVERITY_ATTENTION = "attention"
SEVERITY_INFO = "info"

# Batch statuses that can still hold an operation needing a decision.
UNFINISHED_BATCH_STATUSES = ("running", "interrupted", "partial", "cancelled", "undo_partial")


class RecoveryError(RuntimeError):
    """No guidance is available (for example, an unknown batch)."""


@dataclass(frozen=True)
class RecoveryStep:
    """One file that needs a decision, with the safest next action spelled out."""

    operation: OperationRecord
    severity: str
    title: str
    detail: str
    next_action: str

    @property
    def source(self) -> str:
        return self.operation.source_path or ""

    @property
    def destination(self) -> str:
        return self.operation.destination_path or ""

    @property
    def text(self) -> str:
        return f"{self.title}: {self.detail} Next step: {self.next_action}"


def unfinished_batches(history: HistoryRepository, *, limit: int | None = None) -> list[BatchRecord]:
    """Batches that ended running, interrupted, partly applied or cancelled."""
    return history.list_recovery_batches(limit)


def recovery_steps(batch_id: str, history: HistoryRepository) -> tuple[RecoveryStep, ...]:
    """Guidance for every move of ``batch_id`` that did not finish cleanly."""
    batch = history.get_batch(batch_id)
    if batch is None:
        raise RecoveryError("That batch no longer exists in history.")
    steps = [step for step in (_step_for(batch, op) for op in history.list_operations(batch_id)) if step is not None]
    logger.info("Recovery guidance for batch %s: %d item(s)", batch_id, len(steps))
    return tuple(steps)


def _step_for(batch: BatchRecord, operation: OperationRecord) -> RecoveryStep | None:
    if operation.op_type != OP_MOVE:
        return None
    status = operation.status
    undo = status == STATUS_SUCCESS and operation.undo_status in (UNDO_PENDING, UNDO_FAILED)
    if not undo and status not in (STATUS_PENDING, STATUS_INTERRUPTED, STATUS_FAILED):
        return None

    if undo:
        title = "Undo outcome was not recorded" if operation.undo_status == UNDO_PENDING else "Undo did not finish"
        reason = operation.undo_error
    else:
        title = "This move failed" if status == STATUS_FAILED else "This move was interrupted"
        reason = operation.error
    source, destination = operation.source_path or "", operation.destination_path or ""
    at_source = _location(source, batch, operation)
    at_destination = _location(destination, batch, operation)
    detail = f"Original '{source}': {at_source}. Organized '{destination}': {at_destination}."
    if reason:
        detail = f"{reason}. {detail}"

    if at_source == _VERIFIED and at_destination == _MISSING:
        action = (
            "The recorded file is back at the original location. No further move is needed for this file."
            if undo
            else "The recorded file is at its original location. Resolve the cause and run the preview again."
        )
    elif at_source == _MISSING and at_destination == _VERIFIED:
        action = (
            "The recorded file is still at the organized location. Resolve the cause before retrying Undo."
            if undo
            else "The recorded file is at the organized location, but Undo cannot reverse an unrecorded move. "
            "Leave it there or check both paths before restoring it yourself."
        )
    elif at_source == at_destination == _VERIFIED:
        action = (
            "Both entries match the recorded file and may be hard links. Leave both in place until you inspect them."
        )
    elif at_source == at_destination == _MISSING:
        action = f"Search for '{Path(source).name}'. Do not run the batch again until the file is located."
    else:
        action = "Check both paths manually before retrying or removing anything; the outcome is not verified."
    return RecoveryStep(operation, SEVERITY_ATTENTION, title, detail, action)


_VERIFIED = "verified against the recorded file"
_MISSING = "missing"
_UNVERIFIED = "unverified (may not be the recorded file)"


def _location(path: str, batch: BatchRecord, operation: OperationRecord) -> str:
    if not path:
        return _UNVERIFIED
    entry, root = Path(path), Path(batch.root_path)
    try:
        if not writable_parts(entry, root):
            return _UNVERIFIED
        with bound_directory(entry.parent, root, root_identity=batch.root_identity) as parent:
            stats = parent.stat_entry(entry.name)
    except FileNotFoundError:
        return _MISSING
    except (OSError, ValueError):
        return _UNVERIFIED
    if (
        operation.file_id is None
        or not stat.S_ISREG(stats.st_mode)
        or is_reparse_entry(stats)
        or file_identity(stats, entry) != operation.file_id
        or (operation.size_bytes is not None and stats.st_size != operation.size_bytes)
        or (operation.modified_ns is not None and stats.st_mtime_ns != operation.modified_ns)
    ):
        return _UNVERIFIED
    return _VERIFIED
