import os
import threading
from pathlib import Path

import pytest

from smart_file_organizer.core.duplicates import PARTIAL_HASH_BYTES, find_duplicates
from smart_file_organizer.core.models import ScanOptions

from .conftest import make_file, symlinks_supported


def test_identical_contents_are_grouped(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "a.bin", b"same content")
    make_file(workspace / "sub" / "b.bin", b"same content")
    make_file(workspace / "c.bin", b"different!!")
    report = find_duplicates(workspace, recursive_options)
    assert len(report.groups) == 1
    group = report.groups[0]
    assert {file.path.name for file in group.files} == {"a.bin", "b.bin"}
    assert group.recoverable_bytes == len(b"same content")


def test_same_size_different_content_is_not_duplicate(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "one.dat", b"AAAAAAAA")
    make_file(workspace / "two.dat", b"BBBBBBBB")
    report = find_duplicates(workspace, recursive_options)
    assert report.groups == ()


def test_same_name_different_content_is_not_duplicate(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "x" / "report.pdf", b"first version")
    make_file(workspace / "y" / "report.pdf", b"second, longer version")
    assert find_duplicates(workspace, recursive_options).groups == ()


def test_difference_after_partial_hash_window_is_detected(workspace: Path, recursive_options: ScanOptions) -> None:
    prefix = b"p" * (PARTIAL_HASH_BYTES + 10)
    make_file(workspace / "big1.bin", prefix + b"tail-one")
    make_file(workspace / "big2.bin", prefix + b"tail-two")
    assert find_duplicates(workspace, recursive_options).groups == ()


def test_large_identical_files_are_grouped(workspace: Path, recursive_options: ScanOptions) -> None:
    data = os.urandom(PARTIAL_HASH_BYTES * 3)
    make_file(workspace / "one.bin", data)
    make_file(workspace / "two.bin", data)
    report = find_duplicates(workspace, recursive_options)
    assert len(report.groups) == 1


def test_hard_links_are_not_counted_as_recoverable(workspace: Path, recursive_options: ScanOptions) -> None:
    original = make_file(workspace / "orig.bin", b"shared bytes")
    try:
        os.link(original, workspace / "linked.bin")
    except OSError:
        pytest.skip("Hard links are not supported here")
    report = find_duplicates(workspace, recursive_options)
    assert len(report.groups) == 1
    assert report.groups[0].distinct_copies == 1
    assert report.recoverable_bytes == 0


def test_symlinks_are_excluded(workspace: Path, tmp_path: Path, recursive_options: ScanOptions) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    make_file(workspace / "real.bin", b"content")
    os.symlink(workspace / "real.bin", workspace / "alias.bin")
    assert find_duplicates(workspace, recursive_options).groups == ()


def test_empty_files_are_not_reported(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "e1.txt", b"")
    make_file(workspace / "e2.txt", b"")
    assert find_duplicates(workspace, recursive_options).groups == ()


def test_recoverable_space_counts_extra_copies(workspace: Path, recursive_options: ScanOptions) -> None:
    for name in ("a", "b", "c"):
        make_file(workspace / f"{name}.bin", b"0123456789")
    report = find_duplicates(workspace, recursive_options)
    assert report.groups[0].distinct_copies == 3
    assert report.recoverable_bytes == 20
    assert report.duplicate_file_count == 3


def test_duplicates_are_never_deleted(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "a.bin", b"dup")
    make_file(workspace / "b.bin", b"dup")
    find_duplicates(workspace, recursive_options)
    assert (workspace / "a.bin").exists() and (workspace / "b.bin").exists()


def test_cancellation_returns_partial_report(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "a.bin", b"dup")
    make_file(workspace / "b.bin", b"dup")
    event = threading.Event()
    event.set()
    report = find_duplicates(workspace, recursive_options, cancel_event=event)
    assert report.cancelled
    assert report.groups == ()


class _EditDuringRead:
    """Handle wrapper that rewrites the file on disk right after its first read."""

    def __init__(self, real, path: Path) -> None:
        self._real = real
        self._path = path
        self._edited = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        self._real.close()

    def fileno(self) -> int:
        return self._real.fileno()

    def read(self, size: int = -1) -> bytes:
        data = self._real.read(size)
        if not self._edited and self._path.name == "b.bin":
            self._edited = True
            self._path.write_bytes(b"dup but edited")
        return data


def test_file_changing_while_hashed_is_excluded(
    workspace: Path, recursive_options: ScanOptions, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_file_organizer.core import duplicates

    make_file(workspace / "a.bin", b"dup")
    make_file(workspace / "b.bin", b"dup")
    real_open = duplicates.open_read_no_follow

    def open_with_edit(path: Path):
        handle, stats = real_open(path)
        return _EditDuringRead(handle, Path(path)), stats

    monkeypatch.setattr(duplicates, "open_read_no_follow", open_with_edit)
    report = find_duplicates(workspace, recursive_options)
    assert report.groups == ()
    assert any("Changed while being compared" in issue.message for issue in report.issues)
