"""Human-friendly formatting of sizes and timestamps."""

from __future__ import annotations

from datetime import datetime

_UNITS = ("B", "KB", "MB", "GB", "TB")
_MEGABYTE = 1024 * 1024


def format_size(num_bytes: int) -> str:
    """Format a byte count using binary units, e.g. ``1.5 MB``."""
    if num_bytes < 0:
        raise ValueError("Size cannot be negative")
    value = float(num_bytes)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")  # pragma: no cover


def format_timestamp(timestamp: float | None) -> str:
    """Format a POSIX timestamp in local time; ``-`` when unknown."""
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def format_ns_timestamp(nanoseconds: int | None) -> str:
    """Format a nanosecond timestamp (as returned by ``os.stat``)."""
    if nanoseconds is None:
        return "-"
    return format_timestamp(nanoseconds / 1_000_000_000)


def megabytes_to_bytes(megabytes: float) -> int:
    """Convert MiB to bytes, rejecting negative values."""
    if megabytes < 0:
        raise ValueError("Size cannot be negative")
    return int(megabytes * _MEGABYTE)
