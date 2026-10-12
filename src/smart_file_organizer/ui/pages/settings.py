"""Settings: persisted preferences, category rules and exclusions. Validated before saving."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import customtkinter as ctk

from smart_file_organizer.config import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    DEFAULT_CATEGORIES,
    DEFAULT_EXCLUDED_FOLDERS,
    DEFAULT_EXCLUDED_PATTERNS,
    ORGANIZE_BY_DATE,
    ORGANIZE_BY_EXTENSION,
    ORGANIZE_BY_TYPE,
    OTHER_CATEGORY,
    Settings,
)
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.widgets import Banner, Card
from smart_file_organizer.ui.pages.base import (
    BasePage,
    button,
    muted_label,
    parse_positive_float,
)
from smart_file_organizer.utils.paths import (
    UnsafePathError,
    app_data_dir,
    validate_name,
)
from smart_file_organizer.utils.system import open_in_file_manager

MODE_LABELS = {
    "By file type": ORGANIZE_BY_TYPE,
    "By extension": ORGANIZE_BY_EXTENSION,
    "By modified month": ORGANIZE_BY_DATE,
}
COLLISION_LABELS = {"Rename safely (name (2).ext)": COLLISION_RENAME, "Skip conflicting files": COLLISION_SKIP}
THEME_LABELS = {"Dark": "dark", "Light": "light"}


def _split_list(text: str) -> list[str]:
    return [part.strip() for part in text.replace("\n", ",").split(",") if part.strip()]


class SettingsPage(BasePage):
    title = "Settings"
    description = (
        "Preferences are saved to settings.json in your user data folder. Changes apply to new scans and previews."
    )

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_columnconfigure(0, weight=1)
        self._banner = Banner(body)
        self._banner.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        general = Card(body)
        general.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self._section(general, "Appearance and defaults")
        row = ctk.CTkFrame(general, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=(0, 14))
        self._theme = self._option(row, "Theme", list(THEME_LABELS), 120)
        self._mode = self._option(row, "Default organize by", list(MODE_LABELS), 190)
        self._collision = self._option(row, "If a name already exists", list(COLLISION_LABELS), 230)
        self._recursive = ctk.CTkCheckBox(general, text="Include subfolders by default", text_color=theme.TEXT)
        self._recursive.pack(anchor="w", padx=16, pady=(0, 14))

        rules = Card(body)
        rules.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        self._section(rules, "Category destination folders")
        muted_label(rules, "Rename the folder each category is moved into. Names must be plain folder names.").pack(
            anchor="w", padx=16
        )
        grid = ctk.CTkFrame(rules, fg_color="transparent")
        grid.pack(fill="x", padx=16, pady=8)
        self._folder_entries: dict[str, ctk.CTkEntry] = {}
        self._category_checks: dict[str, ctk.CTkCheckBox] = {}
        for index, rule in enumerate((*DEFAULT_CATEGORIES, OTHER_CATEGORY)):
            ctk.CTkLabel(grid, text=rule.name, text_color=theme.TEXT, width=130, anchor="w").grid(
                row=index, column=0, sticky="w", pady=3
            )
            entry = ctk.CTkEntry(grid, width=220, height=30)
            entry.grid(row=index, column=1, sticky="w", padx=8, pady=3)
            self._folder_entries[rule.name] = entry
            check = ctk.CTkCheckBox(grid, text="Enabled", text_color=theme.TEXT)
            check.grid(row=index, column=2, sticky="w", padx=8, pady=3)
            self._category_checks[rule.name] = check
        muted_label(rules, "Unchecked categories are left untouched by Organize and automation.").pack(
            anchor="w", padx=16, pady=(0, 12)
        )

        overrides = Card(body)
        overrides.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self._section(overrides, "Extension overrides")
        muted_label(overrides, "One per line, e.g.  ai = Documents   or   ino = Images").pack(anchor="w", padx=16)
        self._overrides = ctk.CTkTextbox(overrides, height=90, fg_color=theme.SURFACE_ALT, text_color=theme.TEXT)
        self._overrides.pack(fill="x", padx=16, pady=(6, 14))

        exclusions = Card(body)
        exclusions.grid(row=4, column=0, sticky="ew", pady=(0, 12))
        self._section(exclusions, "Exclusions")
        muted_label(exclusions, "Folder names (comma separated) are skipped at any depth.").pack(anchor="w", padx=16)
        self._excluded_folders = ctk.CTkEntry(exclusions, height=32)
        self._excluded_folders.pack(fill="x", padx=16, pady=(4, 8))
        muted_label(exclusions, "File name patterns (comma separated, * and ? allowed).").pack(anchor="w", padx=16)
        self._excluded_patterns = ctk.CTkEntry(exclusions, height=32)
        self._excluded_patterns.pack(fill="x", padx=16, pady=(4, 14))

        large = Card(body)
        large.grid(row=5, column=0, sticky="ew", pady=(0, 12))
        self._section(large, "Large files and automation")
        row2 = ctk.CTkFrame(large, fg_color="transparent")
        row2.pack(fill="x", padx=16, pady=(0, 14))
        muted_label(row2, "Large-file threshold (MB):").pack(side="left")
        self._large_mb = ctk.CTkEntry(row2, width=90, height=32)
        self._large_mb.pack(side="left", padx=8)
        muted_label(row2, "Monitoring check interval (seconds):").pack(side="left", padx=(16, 0))
        self._interval = ctk.CTkEntry(row2, width=90, height=32)
        self._interval.pack(side="left", padx=8)

        data = Card(body)
        data.grid(row=6, column=0, sticky="ew", pady=(0, 12))
        self._section(data, "Where data is stored")
        self._paths = muted_label(data, "")
        self._paths.pack(anchor="w", padx=16)
        button(data, "Open data folder", self._open_data_folder, width=170).pack(anchor="w", padx=16, pady=(8, 14))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=7, column=0, sticky="ew", pady=(0, 16))
        button(actions, "Save settings", self._save, primary=True, width=160).pack(side="left")
        button(actions, "Restore defaults…", self._restore_defaults, width=180).pack(side="left", padx=8)

    def _section(self, card: Card, title: str) -> None:
        ctk.CTkLabel(card, text=title, text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 6)
        )

    def _option(self, master: Any, label: str, values: list[str], width: int) -> ctk.CTkOptionMenu:
        column = ctk.CTkFrame(master, fg_color="transparent")
        column.pack(side="left", padx=(0, 16))
        muted_label(column, label).pack(anchor="w")
        menu = ctk.CTkOptionMenu(column, values=values, width=width, height=32)
        menu.pack(anchor="w", pady=(2, 0))
        return menu

    def on_show(self) -> None:
        self._load()

    def refresh(self) -> None:
        self._load()

    def _load(self) -> None:
        settings = self.ctx.settings
        self._theme.set(_label_for(THEME_LABELS, settings.theme))
        self._mode.set(_label_for(MODE_LABELS, settings.organize_by))
        self._collision.set(_label_for(COLLISION_LABELS, settings.collision_strategy))
        _checkbox(self._recursive, settings.recursive_scan)
        for name, entry in self._folder_entries.items():
            entry.delete(0, "end")
            entry.insert(0, settings.category_folders.get(name, _default_folder(name)))
        for name, check in self._category_checks.items():
            _checkbox(check, name not in settings.disabled_categories)
        self._overrides.delete("1.0", "end")
        self._overrides.insert(
            "end", "\n".join(f"{ext} = {cat}" for ext, cat in sorted(settings.extension_overrides.items()))
        )
        self._excluded_folders.delete(0, "end")
        self._excluded_folders.insert(0, ", ".join(settings.excluded_folders))
        self._excluded_patterns.delete(0, "end")
        self._excluded_patterns.insert(0, ", ".join(settings.excluded_patterns))
        self._large_mb.delete(0, "end")
        self._large_mb.insert(0, f"{settings.large_file_min_mb:g}")
        self._interval.delete(0, "end")
        self._interval.insert(0, str(settings.watch_interval_seconds))
        self._paths.configure(
            text=(
                f"Data folder: {app_data_dir()}\nSettings: {self.ctx.settings_path}\n"
                f"History database: {self.ctx.database.path}\nLog file: {self.ctx.log_path}"
            )
        )

    def _save(self) -> None:
        try:
            updated = self._collect()
        except (ValueError, UnsafePathError) as exc:
            self._banner.show(str(exc), color=theme.DANGER)
            return
        self.ctx.settings = updated
        self.ctx.save_settings()
        theme.configure_appearance(updated.theme)
        self._banner.hide()
        self.app.refresh_views()
        self.notify("Settings saved.")

    def _collect(self) -> Settings:
        folders: dict[str, str] = {}
        for name, entry in self._folder_entries.items():
            text = entry.get().strip()
            default = _default_folder(name)
            if text and text != default:
                folders[name] = validate_name(text)
        overrides: dict[str, str] = {}
        valid_categories = {rule.name for rule in (*DEFAULT_CATEGORIES, OTHER_CATEGORY)}
        for line in self._overrides.get("1.0", "end").splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise ValueError(f"Extension override '{line.strip()}' must look like  ext = Category.")
            raw_extension, raw_category = line.split("=", 1)
            extension = raw_extension.strip().lower().lstrip(".")
            category = raw_category.strip()
            if category not in valid_categories:
                raise ValueError(f"'{category}' is not a category. Use one of: {', '.join(sorted(valid_categories))}.")
            if not extension:
                raise ValueError("Each override needs an extension before the '='.")
            overrides[extension] = category
        disabled = [name for name, check in self._category_checks.items() if not check.get()]
        # Empty exclusion lists fall back to the safe defaults rather than disabling protection.
        folders_list = _split_list(self._excluded_folders.get()) or list(DEFAULT_EXCLUDED_FOLDERS)
        patterns = _split_list(self._excluded_patterns.get()) or list(DEFAULT_EXCLUDED_PATTERNS)
        large = parse_positive_float(self._large_mb.get(), "Large-file threshold", minimum=0.0)
        interval = parse_positive_float(self._interval.get(), "Check interval", minimum=1.0)

        updated = replace(self.ctx.settings)
        updated.theme = THEME_LABELS[self._theme.get()]
        updated.organize_by = MODE_LABELS[self._mode.get()]
        updated.collision_strategy = COLLISION_LABELS[self._collision.get()]
        updated.recursive_scan = bool(self._recursive.get())
        updated.category_folders = folders
        updated.extension_overrides = overrides
        updated.disabled_categories = disabled
        updated.excluded_folders = folders_list
        updated.excluded_patterns = patterns
        updated.large_file_min_mb = large
        updated.watch_interval_seconds = int(interval)
        return updated

    def _restore_defaults(self) -> None:
        if not dialogs.confirm(
            self,
            "Restore defaults",
            "Reset every setting to its default? Watched folders are kept; history is not affected.",
        ):
            return
        defaults = Settings(watch_folders=list(self.ctx.settings.watch_folders))
        self.ctx.settings = defaults
        self.ctx.save_settings()
        theme.configure_appearance(defaults.theme)
        self._load()
        self.app.refresh_views()
        self.notify("Defaults restored.")

    def _open_data_folder(self) -> None:
        try:
            open_in_file_manager(app_data_dir())
        except OSError as exc:
            dialogs.error(self, "Could not open folder", str(exc))


def _label_for(mapping: dict[str, str], value: str) -> str:
    for label, key in mapping.items():
        if key == value:
            return label
    return next(iter(mapping))


def _default_folder(category: str) -> str:
    for rule in (*DEFAULT_CATEGORIES, OTHER_CATEGORY):
        if rule.name == category:
            return rule.folder
    return category


def _checkbox(checkbox: ctk.CTkCheckBox, checked: bool) -> None:
    if checked:
        checkbox.select()
    else:
        checkbox.deselect()
