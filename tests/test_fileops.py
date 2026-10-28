"""Fault-injection tests for the guarded move and read primitives.

The interesting failures here are the ones the filesystem produces *between*
two calls: the source cannot be removed after the link was created, and the
destination is replaced by another program while the rollback runs. Deleting a
file this tool did not create is treated as worse than leaving an extra link.
"""

import os
from pathlib import Path

import pytest

from smart_file_organizer.core import fileops
from smart_file_organizer.core.fileops import (
    MoveRollbackError,
    SourceChangedError,
    move_no_overwrite,
    open_read_no_follow,
)

from .conftest import make_file, symlinks_supported


def _is_source(path: object, workspace: Path) -> bool:
    candidate = Path(os.fspath(path))
    return candidate.name == "source.txt" and candidate.parent == workspace


def _fail_unlink_of_source(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Make removing the source fail, and record every path an unlink was asked for."""
    real_unlink = os.unlink
    requested: list[Path] = []

    def failing_unlink(path, *args, **kwargs):
        requested.append(Path(os.fspath(path)))
        if _is_source(path, workspace):
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fileops.os, "unlink", failing_unlink)
    return requested


def test_failed_source_removal_removes_the_link_it_created(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()
    requested = _fail_unlink_of_source(workspace, monkeypatch)

    with pytest.raises(PermissionError):
        move_no_overwrite(source, destination)

    assert source.read_bytes() == b"payload", "the original must survive a failed move"
    assert not destination.exists(), "the extra link this move created must be removed again"
    assert [path.name for path in requested] == ["source.txt", "source.txt"]


def test_rollback_never_deletes_a_destination_replaced_by_another_program(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()
    real_unlink = os.unlink
    replacement = b"written by another program"

    def failing_unlink(path, *args, **kwargs):
        if _is_source(path, workspace):
            # The instant our removal of the source fails, somebody else takes
            # over the destination path with a file of their own.
            real_unlink(destination)
            destination.write_bytes(replacement)
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(fileops.os, "unlink", failing_unlink)

    with pytest.raises(MoveRollbackError) as info:
        move_no_overwrite(source, destination)

    assert destination.read_bytes() == replacement, "a file written by another program must survive the rollback"
    assert source.read_bytes() == b"payload"
    assert str(destination) in info.value.recovery
    assert "kept" in info.value.recovery


def test_rollback_keeps_the_link_when_identity_cannot_be_verified(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()
    real_unlink = os.unlink
    real_lstat = os.lstat

    def unlink_failing_on_source(path, *args, **kwargs):
        if _is_source(path, workspace):
            raise PermissionError(13, "Permission denied", str(path))
        return real_unlink(path, *args, **kwargs)

    def lstat_without_inode_numbers(path, *args, **kwargs):
        stats = real_lstat(path, *args, **kwargs)
        if _is_source(path, workspace):
            return stats
        # A filesystem that reports no inode number: identity cannot be proved.
        return os.stat_result(
            (stats.st_mode, 0, 0, stats.st_nlink, 0, 0, stats.st_size, stats.st_atime, stats.st_mtime, stats.st_ctime)
        )

    monkeypatch.setattr(fileops.os, "unlink", unlink_failing_on_source)
    monkeypatch.setattr(fileops.os, "lstat", lstat_without_inode_numbers)

    with pytest.raises(MoveRollbackError):
        move_no_overwrite(source, destination)

    assert destination.exists(), "without proof of identity the extra copy is kept, not deleted"
    assert source.read_bytes() == b"payload"


def test_source_replaced_by_a_symlink_is_refused_not_followed(workspace: Path, tmp_path: Path) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside content")
    link = workspace / "link.txt"
    os.symlink(outside, link)
    destination = workspace / "Documents" / "link.txt"
    destination.parent.mkdir()

    with pytest.raises(OSError) as info:
        move_no_overwrite(link, destination)

    assert "not a regular file" in str(info.value.strerror)
    assert os.path.islink(link), "the link itself must be left alone"
    assert outside.read_bytes() == b"outside content"
    assert not destination.exists()


def test_move_is_refused_when_the_source_is_no_longer_the_scanned_file(workspace: Path) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()

    with pytest.raises(SourceChangedError) as info:
        move_no_overwrite(source, destination, expected_identity="424242:987654321")

    assert "replaced" in str(info.value.strerror)
    assert source.read_bytes() == b"payload"
    assert not destination.exists()


def test_move_accepts_the_identity_recorded_at_scan_time(workspace: Path) -> None:
    from smart_file_organizer.core.scanner import file_identity

    source = make_file(workspace / "source.txt", b"payload")
    identity = file_identity(os.lstat(source), source)
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()

    move_no_overwrite(source, destination, expected_identity=identity)

    assert destination.read_bytes() == b"payload"
    assert not source.exists()


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="O_NOFOLLOW is POSIX-only")
def test_open_read_no_follow_refuses_a_symlink(workspace: Path, tmp_path: Path) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside content")
    link = workspace / "link.txt"
    os.symlink(outside, link)

    with pytest.raises(OSError):
        open_read_no_follow(link)


def test_open_read_no_follow_reports_the_handle_it_opened(workspace: Path) -> None:
    from smart_file_organizer.core.scanner import file_identity

    path = make_file(workspace / "real.txt", b"content")
    handle, stats = open_read_no_follow(path)
    with handle:
        assert handle.read() == b"content"
        assert stats.st_size == len(b"content")
        assert file_identity(stats, path) == file_identity(os.lstat(path), path)


# ----- deterministic source-replacement races (audit P0) -------------------


def test_source_replaced_between_link_and_unlink_is_never_deleted(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = make_file(workspace / "source.txt", b"validated payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()
    real_link = os.link

    def link_then_replace(src, dst, *args, **kwargs):
        real_link(src, dst)
        # Another process replaces the source path after the link exists.
        os.unlink(src)
        Path(src).write_bytes(b"unrelated replacement")

    monkeypatch.setattr(fileops.os, "link", link_then_replace)

    with pytest.raises(MoveRollbackError) as info:
        move_no_overwrite(source, destination)

    assert source.read_bytes() == b"unrelated replacement", "the replacement must never be unlinked"
    assert destination.read_bytes() == b"validated payload", "the only remaining copy of the validated file is kept"
    assert str(source) in info.value.recovery and str(destination) in info.value.recovery


def test_source_replaced_before_link_is_not_moved(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_file(workspace / "source.txt", b"validated payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()
    real_link = os.link

    def replace_then_link(src, dst, *args, **kwargs):
        os.unlink(src)
        Path(src).write_bytes(b"replacement")
        return real_link(src, dst)

    monkeypatch.setattr(fileops.os, "link", replace_then_link)

    with pytest.raises(SourceChangedError):
        move_no_overwrite(source, destination)

    assert source.read_bytes() == b"replacement"
    assert not destination.exists(), "the link to the unvalidated replacement must be rolled back"


def test_failed_rollback_cleanup_is_reported_with_both_paths(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "Documents" / "source.txt"
    destination.parent.mkdir()

    def always_fail_unlink(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(fileops.os, "unlink", always_fail_unlink)

    with pytest.raises(MoveRollbackError) as info:
        move_no_overwrite(source, destination)

    assert destination.exists(), "the un-removable extra link stays, and must be reported, not silently suppressed"
    assert str(source) in info.value.recovery and str(destination) in info.value.recovery
