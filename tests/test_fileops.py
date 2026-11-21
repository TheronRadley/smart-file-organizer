"""Guarded move and read primitives, tested only on synthetic files."""

import errno
import os
from pathlib import Path

import pytest

from smart_file_organizer.core import fileops
from smart_file_organizer.core.boundary import BoundaryError, directory_identity
from smart_file_organizer.core.fileops import SourceChangedError, move_no_overwrite, open_read_no_follow
from smart_file_organizer.core.scanner import file_identity

from .conftest import make_file, symlinks_supported


def test_move_uses_no_link_or_unlink(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no intermediate second link and no cleanup unlink in the move path."""
    source = make_file(workspace / "source.txt", b"original")
    destination = workspace / "destination.txt"

    def forbidden(*args, **kwargs):
        pytest.fail("Safe moves must not use a two-operation link/unlink sequence")

    with monkeypatch.context() as patch:
        patch.setattr(fileops.os, "link", forbidden)
        patch.setattr(fileops.os, "unlink", forbidden)
        move_no_overwrite(source, destination)
    assert not source.exists()
    assert destination.read_bytes() == b"original"


def test_failing_native_move_does_not_clean_up_another_file(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = make_file(workspace / "source.txt", b"original")
    destination = workspace / "destination.txt"

    def competing_entry(*args, **kwargs):
        make_file(destination, b"not ours")
        raise FileExistsError(errno.EEXIST, "Destination already exists")

    monkeypatch.setattr(fileops, "_rename_no_replace", competing_entry)
    with pytest.raises(FileExistsError):
        move_no_overwrite(source, destination)
    assert source.read_bytes() == b"original"
    assert destination.read_bytes() == b"not ours"


def test_unsupported_native_move_fails_without_fallback(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "posix":
        pytest.skip("The native no-replace API is mandatory on POSIX")
    source = make_file(workspace / "source.txt", b"original")
    destination = workspace / "destination.txt"
    monkeypatch.setattr(fileops, "_posix_rename", lambda: None)
    with pytest.raises(OSError, match="atomic no-overwrite"):
        move_no_overwrite(source, destination)
    assert source.read_bytes() == b"original"
    assert not destination.exists()


def test_move_revalidates_size_and_timestamp_even_with_the_same_id(workspace: Path) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "destination.txt"
    old = source.stat()
    identity = file_identity(old, source)
    source.write_bytes(b"changed")  # Same size and inode, but changed in place.
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns + 10_000_000_000))
    assert file_identity(source.lstat(), source) == identity
    with pytest.raises(SourceChangedError, match="modification time"):
        move_no_overwrite(
            source,
            destination,
            expected_identity=identity,
            expected_size=old.st_size,
            expected_modified_ns=old.st_mtime_ns,
        )
    assert source.read_bytes() == b"changed"
    assert not destination.exists()


def test_move_rejects_a_replaced_root(workspace: Path) -> None:
    root_id = directory_identity(workspace)
    workspace.rename(workspace.with_name("previous-root"))
    workspace.mkdir()
    source = make_file(workspace / "source.txt", b"different root")
    with pytest.raises(BoundaryError, match="selected folder changed"):
        move_no_overwrite(source, workspace / "destination.txt", root=workspace, root_identity=root_id)
    assert source.read_bytes() == b"different root"


def test_move_rejects_a_source_link(workspace: Path) -> None:
    if not symlinks_supported(workspace):
        pytest.skip("Symbolic links are not permitted in this environment")
    source = make_file(workspace / "source.txt", b"original")
    link = workspace / "link.txt"
    link.symlink_to(source)
    with pytest.raises(SourceChangedError, match="regular file"):
        move_no_overwrite(link, workspace / "destination.txt")
    assert link.is_symlink()
    assert source.read_bytes() == b"original"


def test_move_is_refused_when_the_source_is_no_longer_the_scanned_file(workspace: Path) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "destination.txt"
    with pytest.raises(SourceChangedError, match="approved file"):
        move_no_overwrite(source, destination, expected_identity="424242:987654321")
    assert source.is_file()
    assert not destination.exists()


def test_move_accepts_the_identity_recorded_at_scan_time(workspace: Path) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "destination.txt"
    identity = file_identity(source.stat(), source)
    move_no_overwrite(source, destination, expected_identity=identity)
    assert not source.exists()
    assert file_identity(destination.stat(), destination) == identity


def test_open_read_no_follow_refuses_a_symlink(workspace: Path) -> None:
    if not symlinks_supported(workspace):
        pytest.skip("Symbolic links are not permitted in this environment")
    source = make_file(workspace / "source.txt", b"payload")
    link = workspace / "link.txt"
    link.symlink_to(source)
    with pytest.raises(OSError):
        open_read_no_follow(link)


def test_open_read_no_follow_reports_the_handle_it_opened(workspace: Path) -> None:
    source = make_file(workspace / "source.txt", b"payload")
    handle, stats = open_read_no_follow(source)
    with handle:
        assert stats.st_size == 7
        assert handle.read() == b"payload"
        assert file_identity(stats, source) == file_identity(source.lstat(), source)


def test_cancellation_during_final_validation_prevents_rename(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from threading import Event

    from smart_file_organizer.core.boundary import BoundDirectory
    from smart_file_organizer.core.fileops import MoveCancelled

    source = make_file(workspace / "source.txt", b"payload")
    destination = workspace / "destination.txt"
    cancel = Event()
    stat_entry = BoundDirectory.stat_entry

    def stat_then_cancel(directory, name):
        stats = stat_entry(directory, name)
        cancel.set()
        return stats

    monkeypatch.setattr(BoundDirectory, "stat_entry", stat_then_cancel)
    with pytest.raises(MoveCancelled):
        move_no_overwrite(source, destination, cancel_event=cancel)
    assert source.read_bytes() == b"payload" and not destination.exists()
