"""Lightweight metadata for the preview panel.

Only file headers are read, and only for images, to obtain pixel dimensions.
No other file content is parsed.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from smart_file_organizer.core.models import FileEntry

_HEADER_READ_LIMIT = 512 * 1024
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "gif", "bmp", "webp"})


@dataclass(frozen=True)
class FileMetadata:
    name: str
    extension: str
    size: int
    created_ts: float | None
    modified_ns: int
    location: str
    category: str
    dimensions: tuple[int, int] | None


def read_metadata(entry: FileEntry) -> FileMetadata:
    dimensions = image_dimensions(entry.path) if entry.extension in _IMAGE_EXTENSIONS else None
    return FileMetadata(
        name=entry.name,
        extension=entry.extension,
        size=entry.size,
        created_ts=entry.created_ts,
        modified_ns=entry.modified_ns,
        location=str(entry.path.parent),
        category=entry.category,
        dimensions=dimensions,
    )


def image_dimensions(path: Path) -> tuple[int, int] | None:
    """Return (width, height) from an image header, or None if unknown or unreadable."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(_HEADER_READ_LIMIT)
    except OSError:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        return struct.unpack("<HH", data[6:10])
    if data.startswith(b"BM") and len(data) >= 26:
        width, height = struct.unpack("<ii", data[18:26])
        return abs(width), abs(height)
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_dimensions(data)
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            return None
        marker = data[index + 1]
        if marker == 0xFF:  # fill byte
            index += 1
            continue
        if marker in _JPEG_SOF_MARKERS and index + 9 <= len(data):
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return width, height
        (segment_length,) = struct.unpack(">H", data[index + 2 : index + 4])
        if segment_length < 2:
            return None
        index += 2 + segment_length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None
