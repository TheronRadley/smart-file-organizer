import os
from pathlib import Path

import pytest

from smart_file_organizer.utils.paths import (
    DATA_DIR_ENV_VAR,
    UnsafePathError,
    app_data_dir,
    ensure_within,
    is_within,
    sanitize_name,
    unique_path,
    validate_name,
)

from .conftest import make_file, symlinks_supported


def test_is_within_accepts_root_and_children(workspace: Path) -> None:
    assert is_within(workspace, workspace)
    assert is_within(workspace / "a" / "b.txt", workspace)


def test_is_within_rejects_traversal_and_sibling_prefix(workspace: Path) -> None:
    assert not is_within(workspace / ".." / "outside.txt", workspace)
    # A sibling whose name merely starts with the root's name must not count as inside.
    sibling = workspace.parent / (workspace.name + "-evil")
    assert not is_within(sibling / "file.txt", workspace)


def test_ensure_within_raises_for_escape(workspace: Path) -> None:
    with pytest.raises(UnsafePathError):
        ensure_within(workspace.parent / "elsewhere.txt", workspace)


def test_symlink_pointing_outside_is_outside(workspace: Path, tmp_path: Path) -> None:
    if not symlinks_supported(tmp_path):
        pytest.skip("Symbolic links are not permitted in this environment")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace / "link"
    os.symlink(outside, link)
    assert not is_within(link / "secret.txt", workspace)


@pytest.mark.parametrize("name", ["Documents", "Café Ünïcode", "日本語", "my.folder", "a b"])
def test_validate_name_accepts_safe_names(name: str) -> None:
    assert validate_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        ".",
        "..",
        "a/b",
        "a\\b",
        "bad:name",
        "trailing.",
        "trailing ",
        "CON",
        "lpt1.txt",
        "x" * 121,
        "tab\tname",
    ],
)
def test_validate_name_rejects_unsafe_names(name: str) -> None:
    with pytest.raises(UnsafePathError):
        validate_name(name)


def test_sanitize_name_makes_extension_folders_safe() -> None:
    assert sanitize_name("weird<ext>") == "weird_ext_"
    assert sanitize_name("CON") == "_CON"
    assert sanitize_name("") == "Other"
    assert sanitize_name("pdf") == "pdf"


def test_unique_path_returns_target_when_free(workspace: Path) -> None:
    target = workspace / "report.pdf"
    assert unique_path(target) == target


def test_unique_path_numbers_collisions(workspace: Path) -> None:
    make_file(workspace / "report.pdf")
    make_file(workspace / "report (2).pdf")
    assert unique_path(workspace / "report.pdf") == workspace / "report (3).pdf"


def test_unique_path_keeps_unicode(workspace: Path) -> None:
    make_file(workspace / "résumé.docx")
    assert unique_path(workspace / "résumé.docx").name == "résumé (2).docx"


def test_app_data_dir_honours_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV_VAR, str(tmp_path / "custom"))
    assert app_data_dir() == tmp_path / "custom"
