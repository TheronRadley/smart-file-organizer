"""Plain-language guidance for operations that did not finish cleanly.

Files are moved one at a time and the filesystem offers no multi-file
transaction, so a batch can end partly applied: the application was closed
during a move, a file was locked, or a destination turned out to be occupied.
History records the original and the new location of every move, so the safest
next step can be stated per file instead of leaving the user to guess.

Everything here is read-only. The recommended action is always the one that
cannot lose data: where an interrupted move left two copies, both are named and
neither is removed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from smart_file_organizer.core.history import HistoryRepository
from smart_file_organizer.core.models import (
    OP_MOVE,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_PENDING,
    STATUS_SUCCESS,
    UNDO_FAILED,
    BatchRecord,
    OperationRecord,
)

logger = logging.getLogger(__name__)

SEVERITY_ATTENTION = "attention"
SEVERITY_INFO = "info"

# Batch statuses that can still hold an operation needing a decision.
UNFINISHED_BATCH_STATUSES = ("running", "interrupted", "partial", "cancelled")


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


def unfinished_batches(history: HistoryRepository, *, limit: int = 200) -> list[BatchRecord]:
    """Batches that ended running, interrupted, partly applied or cancelled."""
    return [batch for batch in history.list_batches(limit) if batch.status in UNFINISHED_BATCH_STATUSES]


def recovery_steps(batch_id: str, history: HistoryRepository) -> tuple[RecoveryStep, ...]:
    """Guidance for every move of ``batch_id`` that did not finish cleanly."""
    batch = history.get_batch(batch_id)
    if batch is None:
        raise RecoveryError("That batch no longer exists in history.")
    steps = [step for step in (_step_for(op) for op in history.list_operations(batch_id)) if step is not None]
    logger.info("Recovery guidance for batch %s: %d item(s)", batch_id, len(steps))
    return tuple(steps)


def _step_for(operation: OperationRecord) -> RecoveryStep | None:
    if operation.op_type != OP_MOVE:
        return None
    if operation.status in (STATUS_PENDING, STATUS_INTERRUPTED):
        return _interrupted(operation)
    if operation.status == STATUS_FAILED:
        return _failed(operation)
    if operation.status == STATUS_SUCCESS and operation.undo_status == UNDO_FAILED:
        return _undo_failed(operation)
    return None


def _interrupted(operation: OperationRecord) -> RecoveryStep:
    source, destination = operation.source_path or "", operation.destination_path or ""
    at_source, at_destination = _exists(source), _exists(destination)
    if at_source and at_destination:
        detail = (
            f"the move did not finish cleanly and the file is now at both recorded locations, "
            f"'{source}' and '{destination}'."
        )
        action = (
            "Compare the two copies and delete the one you do not need. "
            "Nothing is deleted for you, because either copy could be the newer one."
        )
    elif at_source:
        detail = f"the move never happened, so the file is still at '{source}'."
        action = "Nothing was lost. Resolve the cause and run the preview again."
    elif at_destination:
        detail = f"the move finished but was not recorded, so the file is at '{destination}'."
        action = (
            f"Leave it there, or move it back to '{source}' yourself. "
            "Undo cannot reverse a move that was never recorded."
        )
    else:
        detail = f"the file is at neither recorded location ('{source}' or '{destination}')."
        action = (
            f"Search for '{Path(source).name}' to find it: another program moved, renamed or deleted it. "
            "Do not run the batch again until you know where the file is."
        )
    return RecoveryStep(operation, SEVERITY_ATTENTION, "This move was interrupted", detail, action)


def _failed(operation: OperationRecord) -> RecoveryStep:
    source, destination = operation.source_path or "", operation.destination_path or ""
    reason = operation.error or "an unreported error"
    if _exists(source):
        detail = f"the move failed ({reason}) and the file was left at '{source}'."
        action = "Resolve the reported cause - access rights, a full disk, an occupied destination - and run it again."
    elif _exists(destination):
        detail = f"the move failed ({reason}) but the file is at '{destination}'."
        action = "Check that this is the file you expected before running anything else."
    else:
        detail = f"the move failed ({reason}) and the file is at neither '{source}' nor '{destination}'."
        action = "Locate the file before retrying; do not run the batch again until you know where it is."
    return RecoveryStep(operation, SEVERITY_ATTENTION, "This move failed", detail, action)


def _undo_failed(operation: OperationRecord) -> RecoveryStep:
    source, destination = operation.source_path or "", operation.destination_path or ""
    reason = operation.undo_error or "an unreported error"
    return RecoveryStep(
        operation,
        SEVERITY_ATTENTION,
        "Undo did not finish",
        f"the file could not be moved back ({reason}) and is still at '{destination}'.",
        f"Resolve the cause and run Undo again, or move it back to '{source}' yourself.",
    )


def _exists(path: str) -> bool:
    """True when something (file, folder or link) answers to ``path``."""
    if not path:
        return False
    try:
        return os.path.lexists(path)
    except (OSError, ValueError):
        return False
