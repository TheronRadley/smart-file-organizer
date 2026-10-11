"""Extension-based file classification.

Classification never opens a file: the category is derived from the file name
only. Unknown or missing extensions fall into the ``Other`` category.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from smart_file_organizer.config import (
    DEFAULT_CATEGORIES,
    OTHER_CATEGORY,
    CategoryRule,
    Settings,
    folder_name_for,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Classification:
    category: str
    folder: str


def extension_of(name: str) -> str:
    """Return the lower-case extension of a file name without the dot ("" if none)."""
    return os.path.splitext(name)[1][1:].lower()


class Classifier:
    """Maps file names to categories and category names to destination folders."""

    def __init__(
        self,
        rules: Iterable[CategoryRule] = DEFAULT_CATEGORIES,
        *,
        extension_overrides: Mapping[str, str] | None = None,
        folder_overrides: Mapping[str, str] | None = None,
    ) -> None:
        self._categories: dict[str, CategoryRule] = {}
        self._extension_to_category: dict[str, str] = {}
        for rule in (*rules, OTHER_CATEGORY):
            self._categories[rule.name] = rule
            for extension in rule.extensions:
                self._extension_to_category.setdefault(extension.lower().lstrip("."), rule.name)

        for extension, category in (extension_overrides or {}).items():
            normalised = extension.lower().lstrip(".")
            if category in self._categories and normalised:
                self._extension_to_category[normalised] = category
            else:
                logger.warning("Ignoring extension override %s -> %s", extension, category)

        self._folders: dict[str, str] = {
            name: folder_name_for(name, rule.folder, dict(folder_overrides or {}))
            for name, rule in self._categories.items()
        }

    @classmethod
    def from_settings(cls, settings: Settings) -> Classifier:
        return cls(
            DEFAULT_CATEGORIES,
            extension_overrides=settings.extension_overrides,
            folder_overrides=settings.category_folders,
        )

    @property
    def category_names(self) -> list[str]:
        return list(self._categories)

    def folder_for_category(self, category: str) -> str:
        return self._folders[category]

    def managed_folder_names(self) -> set[str]:
        """Folder names this classifier may create, in case-folded form."""
        return {folder.casefold() for folder in self._folders.values()}

    def category_for_name(self, file_name: str) -> str:
        return self._extension_to_category.get(extension_of(file_name), OTHER_CATEGORY.name)

    def classify(self, file_name: str) -> Classification:
        category = self.category_for_name(file_name)
        return Classification(category=category, folder=self._folders[category])
