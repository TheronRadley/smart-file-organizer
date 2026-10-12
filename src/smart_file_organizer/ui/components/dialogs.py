"""Native dialogs used for confirmations, folder selection and messages."""

from __future__ import annotations

from tkinter import filedialog, messagebox
from typing import Any


def choose_folder(parent: Any, title: str = "Choose a folder") -> str:
    """Native folder picker. Returns an empty string when cancelled."""
    return filedialog.askdirectory(parent=parent, title=title, mustexist=True) or ""


def confirm(parent: Any, title: str, message: str) -> bool:
    return bool(messagebox.askyesno(title, message, icon="warning", parent=parent))


def info(parent: Any, title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=parent)


def error(parent: Any, title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=parent)
