from pathlib import Path

from smart_file_organizer.core.large_files import find_large_files
from smart_file_organizer.core.models import ScanIssue, ScanOptions, ScanResult
from smart_file_organizer.core.scanner import scan_folder

from .conftest import make_file


def test_minimum_size_and_descending_order(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "small.bin", b"x" * 10)
    make_file(workspace / "medium.bin", b"x" * 500)
    make_file(workspace / "large.bin", b"x" * 2000)
    scan = scan_folder(workspace, recursive_options)
    report = find_large_files(scan, min_bytes=100)
    assert [f.name for f in report.files] == ["large.bin", "medium.bin"]
    assert not report.incomplete


def test_extension_filter(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "movie.mkv", b"x" * 300)
    make_file(workspace / "clip.MP4", b"x" * 300)
    make_file(workspace / "pic.png", b"x" * 300)
    report = find_large_files(scan_folder(workspace, recursive_options), min_bytes=1, extensions=[".mkv", "mp4"])
    assert {f.name for f in report.files} == {"movie.mkv", "clip.MP4"}


def test_limit_is_applied_after_sorting(workspace: Path, recursive_options: ScanOptions) -> None:
    for size in (100, 200, 300):
        make_file(workspace / f"f{size}.bin", b"x" * size)
    report = find_large_files(scan_folder(workspace, recursive_options), min_bytes=1, limit=2)
    assert [f.size for f in report.files] == [300, 200]


def test_access_errors_mark_results_incomplete(workspace: Path, options: ScanOptions) -> None:
    make_file(workspace / "big.bin", b"x" * 50)
    scan = scan_folder(workspace, options)
    degraded = ScanResult(
        root=scan.root,
        options=scan.options,
        entries=scan.entries,
        folders_scanned=scan.folders_scanned,
        skipped_links=0,
        skipped_excluded=0,
        skipped_other=0,
        issues=[ScanIssue(str(workspace / "locked"), "Access denied")],
    )
    report = find_large_files(degraded, min_bytes=1)
    assert report.incomplete
    assert len(report.files) == 1


def test_large_files_are_never_deleted(workspace: Path, recursive_options: ScanOptions) -> None:
    make_file(workspace / "huge.bin", b"x" * 1000)
    find_large_files(scan_folder(workspace, recursive_options), min_bytes=1)
    assert (workspace / "huge.bin").exists()
