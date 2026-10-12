import os
import threading
from pathlib import Path

import pytest

from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.scanner import ScanError, scan_folder

from .conftest import is_root_user, make_file, symlinks_supported


def _names(result) -> set[str]:
    return {entry.relative_path for entry in result.entries}


def test_non_recursive_scan_only_sees_top_level(workspace: Path, options: ScanOptions) -> None:
    make_file(workspace / "top.pdf")
    make_file(workspace / "nested" / "inner.pdf")
    result = scan_folder(workspace, options)
    assert _names(result) == {"top.pdf"}
    assert result.folders_scanned == 2  # root plus the nested folder that was seen but not descended into


def test_recursive_scan_sees_nested_files(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "top.pdf")
    make_file(workspace / "a" / "b" / "deep.txt")
    result = scan_folder(workspace, recursive_options)
    assert _names(result) == {"top.pdf", "a/b/deep.txt"}
    assert result.folders_scanned == 3


def test_excluded_folders_and_patterns_are_skipped(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "keep.txt")
    make_file(workspace / ".git" / "config")
    make_file(workspace / "node_modules" / "pkg" / "index.js")
    make_file(workspace / "partial.crdownload")
    make_file(workspace / "~$draft.docx")
    make_file(workspace / ".hidden")
    result = scan_folder(workspace, recursive_options)
    assert _names(result) == {"keep.txt"}
    assert result.skipped_excluded >= 4


def test_custom_exclusion_pattern(workspace: Path) -> None:
    make_file(workspace / "keep.txt")
    make_file(workspace / "secret-notes.txt")
    options = ScanOptions(excluded_patterns=("secret*",))
    assert _names(scan_folder(workspace, options)) == {"keep.txt"}


def test_unicode_names_are_preserved(workspace: Path, options: ScanOptions) -> None:
    make_file(workspace / "résumé 日本語.pdf", b"abc")
    result = scan_folder(workspace, options)
    (entry,) = result.entries
    assert entry.name == "résumé 日本語.pdf"
    assert entry.extension == "pdf"
    assert entry.category == "Documents"
    assert entry.size == 3


def test_empty_directories_are_counted_not_listed(workspace: Path, recursive_options: ScanOptions) -> None:
    (workspace / "empty" / "nested").mkdir(parents=True)
    result = scan_folder(workspace, recursive_options)
    assert result.entries == []
    assert result.folders_scanned == 3


def test_totals_and_category_distribution(workspace: Path, options: ScanOptions) -> None:
    make_file(workspace / "a.pdf", b"x" * 10)
    make_file(workspace / "b.pdf", b"x" * 20)
    make_file(workspace / "c.mp3", b"x" * 5)
    result = scan_folder(workspace, options)
    assert result.file_count == 3
    assert result.total_bytes == 35
    assert result.category_counts == {"Documents": 2, "Audio": 1}
    assert result.category_bytes == {"Documents": 30, "Audio": 5}


def test_symlinks_are_never_followed(workspace: Path, tmp_path: Path, recursive_options: ScanOptions) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside"
    make_file(outside / "private.pdf")
    make_file(workspace / "real.pdf")
    os.symlink(outside, workspace / "linked_dir")
    os.symlink(outside / "private.pdf", workspace / "linked_file.pdf")
    result = scan_folder(workspace, recursive_options)
    assert _names(result) == {"real.pdf"}
    assert result.skipped_links == 2


def test_missing_root_raises(tmp_path: Path, options: ScanOptions) -> None:
    with pytest.raises(ScanError):
        scan_folder(tmp_path / "does-not-exist", options)


def test_file_as_root_raises(workspace: Path, options: ScanOptions) -> None:
    file_path = make_file(workspace / "file.txt")
    with pytest.raises(ScanError):
        scan_folder(file_path, options)


def test_symlinked_root_is_refused(workspace: Path, tmp_path: Path, options: ScanOptions) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    link = tmp_path / "root-link"
    os.symlink(workspace, link)
    with pytest.raises(ScanError):
        scan_folder(link, options)


@pytest.mark.skipif(
    is_root_user() or os.name == "nt", reason="Requires POSIX permission bits that root or Windows ignore"
)
def test_unreadable_subfolder_is_reported_not_fatal(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "visible.txt")
    locked = workspace / "locked"
    make_file(locked / "hidden.txt")
    locked.chmod(0o000)
    try:
        result = scan_folder(workspace, recursive_options)
    finally:
        locked.chmod(0o755)
    assert _names(result) == {"visible.txt"}
    assert any("locked" in issue.path for issue in result.issues)


def test_cancellation_stops_the_scan(workspace: Path, options: ScanOptions) -> None:
    for index in range(5):
        make_file(workspace / f"f{index}.txt")
    event = threading.Event()
    event.set()
    result = scan_folder(workspace, options, cancel_event=event)
    assert result.cancelled
    assert not result.complete


def test_progress_callback_is_called(workspace: Path, options: ScanOptions) -> None:
    make_file(workspace / "one.txt")
    calls = []
    scan_folder(workspace, options, progress=calls.append)
    assert calls and calls[-1].files_seen == 1
