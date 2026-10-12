import struct
from pathlib import Path

from smart_file_organizer.core.metadata import image_dimensions, read_metadata
from smart_file_organizer.core.models import FileEntry

from .conftest import make_file


def test_png_dimensions(workspace: Path) -> None:
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 640, 480)
    path = make_file(workspace / "a.png", header + b"\x00" * 20)
    assert image_dimensions(path) == (640, 480)


def test_gif_dimensions(workspace: Path) -> None:
    path = make_file(workspace / "a.gif", b"GIF89a" + struct.pack("<HH", 320, 200) + b"\x00" * 10)
    assert image_dimensions(path) == (320, 200)


def test_bmp_dimensions(workspace: Path) -> None:
    data = bytearray(54)
    data[0:2] = b"BM"
    struct.pack_into("<ii", data, 18, 1024, -768)
    path = make_file(workspace / "a.bmp", bytes(data))
    assert image_dimensions(path) == (1024, 768)


def test_jpeg_dimensions_after_app_segment(workspace: Path) -> None:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 600, 800) + b"\x03" + b"\x00" * 9
    path = make_file(workspace / "a.jpg", b"\xff\xd8" + app0 + sof0 + b"\xff\xd9")
    assert image_dimensions(path) == (800, 600)


def test_webp_lossy_dimensions(workspace: Path) -> None:
    body = b"VP8 " + struct.pack("<I", 10) + b"\x00\x00\x00\x9d\x01\x2a" + struct.pack("<HH", 400, 300)
    path = make_file(workspace / "a.webp", b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body)
    assert image_dimensions(path) == (400, 300)


def test_unknown_or_corrupt_images_return_none(workspace: Path) -> None:
    assert image_dimensions(make_file(workspace / "fake.png", b"not really a png")) is None
    assert image_dimensions(workspace / "missing.png") is None


def test_metadata_reads_only_images_for_dimensions(workspace: Path) -> None:
    path = make_file(workspace / "notes.txt", b"hello")
    entry = FileEntry(
        path=path,
        relative_path="notes.txt",
        name="notes.txt",
        extension="txt",
        size=5,
        modified_ns=path.stat().st_mtime_ns,
        created_ts=None,
        file_id=None,
        category="Documents",
    )
    metadata = read_metadata(entry)
    assert metadata.dimensions is None
    assert metadata.location == str(workspace)
