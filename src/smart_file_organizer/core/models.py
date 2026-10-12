"""Plain data structures shared by the core modules and the interface."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Outcome statuses used for plan entries, history rows and batch outcomes.
STATUS_READY = "ready"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_PENDING = "pending"
STATUS_INTERRUPTED = "interrupted"
STATUS_WOULD_MOVE = "would_move"

# Operation types recorded in history.
OP_MOVE = "move"
OP_CREATE_FOLDER = "create_folder"

# Batch kinds.
BATCH_ORGANIZE = "organize"
BATCH_WATCH = "watch"

# Undo statuses.
UNDO_DONE = "undone"
UNDO_FAILED = "failed"
UNDO_KEPT = "kept"


@dataclass(frozen=True)
class FileEntry:
    """A regular file discovered by the scanner."""

    path: Path
    relative_path: str  # POSIX-style, relative to the scanned root
    name: str
    extension: str  # lower-case, without the dot; "" when none
    size: int
    modified_ns: int
    created_ts: float | None
    file_id: str | None  # "device:inode" where the platform reports it
    category: str

    @property
    def depth(self) -> int:
        """Number of path components below the scan root (1 for top-level files)."""
        return self.relative_path.count("/") + 1

    @property
    def top_folder(self) -> str | None:
        """First folder component below the root, or None for top-level files."""
        if "/" not in self.relative_path:
            return None
        return self.relative_path.split("/", 1)[0]


@dataclass(frozen=True)
class ScanOptions:
    recursive: bool = False
    excluded_folders: tuple[str, ...] = ()
    excluded_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanIssue:
    path: str
    message: str


@dataclass(frozen=True)
class ScanProgress:
    files_seen: int
    folders_seen: int
    current: str


@dataclass
class ScanResult:
    root: Path
    options: ScanOptions
    entries: list[FileEntry]
    folders_scanned: int
    skipped_links: int
    skipped_excluded: int
    skipped_other: int
    issues: list[ScanIssue]
    cancelled: bool = False

    @property
    def file_count(self) -> int:
        return len(self.entries)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def category_counts(self) -> dict[str, int]:
        return dict(Counter(entry.category for entry in self.entries))

    @property
    def category_bytes(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for entry in self.entries:
            totals[entry.category] = totals.get(entry.category, 0) + entry.size
        return totals

    @property
    def complete(self) -> bool:
        """True when the scan finished and no entry could not be read."""
        return not self.cancelled and not self.issues


@dataclass(frozen=True)
class PlannedOperation:
    """One proposed move in an organization preview."""

    index: int
    source: Path
    source_relative: str
    size: int
    modified_ns: int
    file_id: str | None
    category: str
    destination_folder: str  # folder name relative to the root; "" when skipped
    destination: Path | None  # proposed final path; None when skipped
    status: str  # STATUS_READY or STATUS_SKIPPED
    reason: str = ""


@dataclass(frozen=True)
class OrganizePlan:
    root: Path
    organize_by: str
    collision_strategy: str
    operations: tuple[PlannedOperation, ...]

    @property
    def ready(self) -> tuple[PlannedOperation, ...]:
        return tuple(op for op in self.operations if op.status == STATUS_READY)

    @property
    def skipped(self) -> tuple[PlannedOperation, ...]:
        return tuple(op for op in self.operations if op.status != STATUS_READY)

    @property
    def bytes_to_move(self) -> int:
        return sum(op.size for op in self.ready)


@dataclass(frozen=True)
class OperationOutcome:
    index: int
    source: str
    destination: str | None
    status: str
    reason: str
    size: int


@dataclass
class BatchOutcome:
    """Result of applying (or dry-running) a plan."""

    root: Path
    dry_run: bool
    batch_id: str | None
    outcomes: list[OperationOutcome] = field(default_factory=list)
    cancelled: bool = False

    def count(self, status: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == status)

    @property
    def success_count(self) -> int:
        return self.count(STATUS_SUCCESS)

    @property
    def failed_count(self) -> int:
        return self.count(STATUS_FAILED)

    @property
    def skipped_count(self) -> int:
        return self.count(STATUS_SKIPPED)


@dataclass(frozen=True)
class BatchRecord:
    id: str
    kind: str
    root_name: str
    root_path: str
    dry_run: bool
    status: str
    created_at: float
    finished_at: float | None
    note: str | None
    operation_count: int = 0


@dataclass(frozen=True)
class OperationRecord:
    id: int
    batch_id: str
    op_type: str
    source_path: str | None
    destination_path: str | None
    size_bytes: int | None
    modified_ns: int | None
    file_id: str | None
    status: str
    error: str | None
    created_at: float
    undo_status: str | None
    undo_error: str | None
    undone_at: float | None


@dataclass(frozen=True)
class ScanRecord:
    id: int
    root_name: str
    file_count: int
    folder_count: int
    total_bytes: int
    category_counts: dict[str, int]
    issue_count: int
    created_at: float
