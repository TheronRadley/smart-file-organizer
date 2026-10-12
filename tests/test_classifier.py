from smart_file_organizer.config import DEFAULT_CATEGORIES, Settings, settings_from_dict
from smart_file_organizer.core.classifier import Classifier, extension_of


def test_known_extensions_map_to_default_categories(classifier: Classifier) -> None:
    expectations = {
        "report.pdf": "Documents",
        "vacation.JPG": "Images",
        "clip.mkv": "Videos",
        "song.flac": "Audio",
        "project.zip": "Archives",
        "main.rs": "Code",
        "budget.xlsx": "Spreadsheets",
        "deck.pptx": "Presentations",
        "setup.msi": "Executables",
        "font.woff2": "Fonts",
    }
    for name, category in expectations.items():
        assert classifier.classify(name).category == category, name


def test_default_categories_cover_the_specified_extensions() -> None:
    covered = {ext for rule in DEFAULT_CATEGORIES for ext in rule.extensions}
    required = {
        "pdf",
        "docx",
        "doc",
        "txt",
        "rtf",
        "odt",
        "png",
        "jpg",
        "jpeg",
        "gif",
        "svg",
        "webp",
        "bmp",
        "tiff",
        "mp4",
        "mkv",
        "mov",
        "avi",
        "webm",
        "mp3",
        "wav",
        "flac",
        "aac",
        "ogg",
        "m4a",
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "py",
        "js",
        "ts",
        "html",
        "css",
        "json",
        "xml",
        "java",
        "cpp",
        "c",
        "go",
        "rs",
        "xlsx",
        "xls",
        "csv",
        "ods",
        "pptx",
        "ppt",
        "key",
        "exe",
        "msi",
        "apk",
        "dmg",
        "ttf",
        "otf",
        "woff",
        "woff2",
    }
    assert required <= covered


def test_unknown_and_missing_extensions_fall_into_other(classifier: Classifier) -> None:
    assert classifier.classify("mystery.xyz123").category == "Other"
    assert classifier.classify("README").category == "Other"
    assert classifier.classify(".bashrc").category == "Other"


def test_multi_part_extension_uses_last_segment(classifier: Classifier) -> None:
    assert extension_of("backup.tar.gz") == "gz"
    assert classifier.classify("backup.tar.gz").category == "Archives"


def test_extension_override_and_folder_rename() -> None:
    classifier = Classifier(
        extension_overrides={".ai": "Documents"},
        folder_overrides={"Documents": "Papers"},
    )
    assert classifier.classify("design.ai").category == "Documents"
    assert classifier.folder_for_category("Documents") == "Papers"


def test_invalid_folder_override_falls_back_to_default() -> None:
    classifier = Classifier(folder_overrides={"Images": "bad/name"})
    assert classifier.folder_for_category("Images") == "Images"


def test_invalid_override_category_is_ignored() -> None:
    classifier = Classifier(extension_overrides={"xyz": "NoSuchCategory"})
    assert classifier.classify("file.xyz").category == "Other"


def test_settings_build_classifier() -> None:
    settings = Settings()
    settings.category_folders = {"Audio": "Music"}
    assert Classifier.from_settings(settings).folder_for_category("Audio") == "Music"


def test_settings_loader_drops_invalid_values() -> None:
    loaded = settings_from_dict({"collision_strategy": "overwrite", "recursive_scan": "yes", "large_file_min_mb": 5})
    assert loaded.collision_strategy == "rename"
    assert loaded.recursive_scan is False
    assert loaded.large_file_min_mb == 5.0
