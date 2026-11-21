"""Read-only cancellation flags, including work owned by several callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class AnyCancellation:
    flags: tuple[Cancellation, ...]

    def is_set(self) -> bool:
        return any(flag.is_set() for flag in self.flags)
