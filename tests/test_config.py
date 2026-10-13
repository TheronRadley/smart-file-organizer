import json
from pathlib import Path

from smart_file_organizer.config import (
    DEFAULT_EXCLUDED_PATTERNS,
    Settings,
    folder_name_for,
    load_settings,
    save_settings,
    settings_from_dict,
)
from smart_file_organizer.core.classifier import Classifier
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.scanner import scan_folder

from .conftest import make_file


def test_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = Settings(
        organize_by="extension", collision_strategy="skip", watch_folders=["C:/Downloads"], theme="light"
    )
    save_settings(original, path)
    loaded = load_settings(path)
    assert loaded == original


def test_missing_settings_file_gives_defaults(tmp_path: Path) -> None:
    assert load_settings(tmp_path / "absent.json") == Settings()


def test_corrupt_settings_file_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"future_option": True, "theme": "light"}), encoding="utf-8")
    loaded = load_settings(path)
    assert loaded.theme == "light"


def test_invalid_enum_values_fall_back() -> None:
    loaded = settings_from_dict({"organize_by": "colour", "theme": "neon", "watch_interval_seconds": 0})
    assert loaded.organize_by == "type"
    assert loaded.theme == "dark"
    assert loaded.watch_interval_seconds == 1


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(Settings(), path)
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


# ----- contents are validated, not just container types --------------------


def test_invalid_items_inside_lists_are_dropped() -> None:
    loaded = settings_from_dict({"excluded_folders": [".git", 5, None, "", ["x"], "build"]})
    assert loaded.excluded_folders == [".git", "build"]


def test_invalid_watch_folders_are_dropped() -> None:
    loaded = settings_from_dict({"watch_folders": ["/home/me/Downloads", 42, None, {"a": 1}]})
    assert loaded.watch_folders == ["/home/me/Downloads"]


def test_invalid_disabled_categories_are_dropped() -> None:
    loaded = settings_from_dict({"disabled_categories": [7, "Images", None]})
    assert loaded.disabled_categories == ["Images"]


def test_a_list_given_as_the_wrong_type_falls_back_to_the_default() -> None:
    loaded = settings_from_dict({"excluded_patterns": ".*", "watch_folders": {"a": 1}})
    assert loaded.excluded_patterns == list(DEFAULT_EXCLUDED_PATTERNS)
    assert loaded.watch_folders == []


def test_a_list_with_no_usable_entry_keeps_the_shipped_defaults() -> None:
    # An empty exclusion list would silently weaken the protections, so a
    # settings file whose entries are all unusable must not produce one.
    loaded = settings_from_dict({"excluded_patterns": [None, 7]})
    assert loaded.excluded_patterns == list(DEFAULT_EXCLUDED_PATTERNS)


def test_an_explicitly_empty_list_is_honoured() -> None:
    assert settings_from_dict({"excluded_patterns": []}).excluded_patterns == []


def test_invalid_dictionary_entries_are_dropped() -> None:
    loaded = settings_from_dict({"category_folders": {"Documents": 5, "Images": "", "Videos": "Clips"}})
    assert loaded.category_folders == {"Videos": "Clips"}
    assert folder_name_for("Documents", "Documents", loaded.category_folders) == "Documents"
    assert folder_name_for("Videos", "Videos", loaded.category_folders) == "Clips"


def test_a_dictionary_given_as_the_wrong_type_falls_back_to_the_default() -> None:
    loaded = settings_from_dict({"extension_overrides": ["not", "a", "dict"]})
    assert loaded.extension_overrides == {}


def test_numbers_reject_the_wrong_kind_of_value() -> None:
    loaded = settings_from_dict({"large_file_min_mb": "big", "watch_interval_seconds": True})
    assert loaded.large_file_min_mb == Settings().large_file_min_mb
    assert loaded.watch_interval_seconds == Settings().watch_interval_seconds


def test_an_integer_is_accepted_for_a_float_setting() -> None:
    assert settings_from_dict({"large_file_min_mb": 250}).large_file_min_mb == 250.0


def test_malformed_settings_cannot_crash_a_scan(tmp_path: Path, workspace: Path) -> None:
    """The point of validating contents: a hand-edited file must not break scanning."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "excluded_folders": [".git", 5, None, ["nested"]],
                "excluded_patterns": [".*", 12, {"bad": True}],
                "category_folders": {"Documents": 5, "Images": "Photos"},
                "extension_overrides": {"pdf": 3, "txt": "Documents"},
                "watch_folders": ["/srv/incoming", 99],
                "organize_by": "type",
                "large_file_min_mb": "huge",
            }
        ),
        encoding="utf-8",
    )
    make_file(workspace / "report.pdf")
    make_file(workspace / ".hidden")

    settings = load_settings(path)
    scan = scan_folder(
        workspace,
        ScanOptions(
            recursive=False,
            excluded_folders=tuple(settings.excluded_folders),
            excluded_patterns=tuple(settings.excluded_patterns),
        ),
        Classifier.from_settings(settings),
    )
    assert [entry.name for entry in scan.entries] == ["report.pdf"]
    assert scan.issues == []
