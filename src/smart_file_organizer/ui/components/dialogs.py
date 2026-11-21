"""Native dialogs used for confirmations, folder selection and messages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any

from smart_file_organizer.core.export import Export, ExportError, inspect_export_target, write_export


def choose_folder(parent: Any, title: str = "Choose a folder") -> str:
    """Native folder picker. Returns an empty string when cancelled."""
    return filedialog.askdirectory(parent=parent, title=title, mustexist=True) or ""


EXPORT_FILETYPES: tuple[tuple[str, str], ...] = (
    ("CSV (spreadsheet)", "*.csv"),
    ("JSON", "*.json"),
)


def choose_save_path(
    parent: Any, title: str, default_name: str, filetypes: tuple[tuple[str, str], ...] = EXPORT_FILETYPES
) -> str:
    """Native save-as picker for reports. Returns an empty string when cancelled."""
    return (
        filedialog.asksaveasfilename(
            parent=parent,
            title=title,
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=list(filetypes),
            confirmoverwrite=False,
        )
        or ""
    )


def confirm(parent: Any, title: str, message: str) -> bool:
    return bool(messagebox.askyesno(title, message, icon="warning", parent=parent))


def info(parent: Any, title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=parent)


def error(parent: Any, title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=parent)


def save_report(parent: Any, title: str, default_name: str, report: Callable[[], Export]) -> Path | None:
    """Ask about the particular existing file, then publish without implicit overwrite."""
    path = choose_save_path(parent, title, default_name)
    if not path:
        return None
    try:
        existing = inspect_export_target(path)
        if existing is not None and not confirm(
            parent, "Replace existing file?", f"Replace this file?\n\n{existing.path}"
        ):
            return None
        return write_export(report(), path, replace_existing=existing)
    except ExportError as exc:
        error(parent, "Could not export", str(exc))
        return None
