import json
from pathlib import Path

from smart_file_organizer.config import (
    Settings,
    load_settings,
    save_settings,
    settings_from_dict,
)


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
