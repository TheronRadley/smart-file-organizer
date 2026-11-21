"""Process-local coordination for file operations and maintenance of their journal."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from smart_file_organizer.core.boundary import storage_identity
from smart_file_organizer.utils.paths import normalise

_guard = threading.Lock()
_active: dict[Path, tuple[str | None, set[str]]] = {}
_clearing = False


@contextmanager
def operation_lock(root: Path | str) -> Iterator[bool]:
    """Nonblocking: independent trees can move in parallel, but overlapping ones cannot."""
    key = Path(normalise(root))
    root_identity = _identity(key)
    ancestors = {identity for parent in key.parents if (identity := _identity(parent)) is not None}
    with _guard:
        acquired = not _clearing and not any(
            key.is_relative_to(other)
            or other.is_relative_to(key)
            or (root_identity is not None and (root_identity == other_id or root_identity in other_ancestors))
            or (other_id is not None and other_id in ancestors)
            for other, (other_id, other_ancestors) in _active.items()
        )
        if acquired:
            _active[key] = (root_identity, ancestors)
    try:
        yield acquired
    finally:
        if acquired:
            with _guard:
                del _active[key]


@contextmanager
def history_clear_lock() -> Iterator[None]:
    """Reserve an idle journal until the history deletion transaction finishes."""
    global _clearing
    with _guard:
        if _active or _clearing:
            raise RuntimeError("History cannot be cleared while a file operation is running. Wait and try again.")
        _clearing = True
    try:
        yield
    finally:
        with _guard:
            _clearing = False


def _identity(path: Path) -> str | None:
    try:
        return storage_identity(path.stat())
    except OSError:
        return None
