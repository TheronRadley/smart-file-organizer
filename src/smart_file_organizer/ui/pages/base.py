"""Common page behaviour: header, scrollable body, lifecycle hooks and input validation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import customtkinter as ctk

from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components.widgets import PageHeader

if TYPE_CHECKING:
    from smart_file_organizer.ui.app_window import App


def validate_folder(text: str) -> Path:
    """Return an existing, non-symlink folder path or raise ValueError with a user-facing message."""
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Choose a folder first.")
    path = Path(cleaned).expanduser()
    if not path.is_dir():
        raise ValueError(f"'{cleaned}' is not an existing folder.")
    return path


def parse_positive_float(text: str, label: str, *, minimum: float = 0.0) -> float:
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number.")
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum:g}.")
    return value


class BasePage(ctk.CTkFrame):
    """Base class for pages. Subclasses implement :meth:`build` and optionally the hooks."""

    title = ""
    description = ""
    scrollable = True

    def __init__(self, master: Any, app: App) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.ctx = app.ctx
        self.runner = app.runner
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        PageHeader(self, self.title, self.description).grid(row=0, column=0, sticky="ew", pady=(0, 14))
        if self.scrollable:
            body: ctk.CTkFrame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        else:
            body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        self.body = body
        self.build(body)

    def build(self, body: ctk.CTkFrame) -> None:
        raise NotImplementedError

    def on_show(self) -> None:
        """Called when the page becomes visible."""
        self.refresh()

    def on_hide(self) -> None:
        """Called when another page replaces this one."""

    def refresh(self) -> None:
        """Redraw from shared state. Must be cheap and must not start work."""

    def set_scan_running(self, running: bool) -> None:
        """Called when the shared scan starts or finishes."""

    def notify(self, message: str) -> None:
        self.app.notify(message)


def muted_label(master: Any, text: str, **kwargs: Any) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, text_color=theme.MUTED, justify="left", **kwargs)


def button(
    master: Any, text: str, command: Any, *, primary: bool = False, width: int = 140, **kwargs: Any
) -> ctk.CTkButton:
    if primary:
        colors: dict[str, Any] = {
            "fg_color": theme.BUTTON,
            "text_color": theme.BUTTON_TEXT,
            "hover_color": theme.BUTTON_HOVER,
        }
    else:
        colors = {"fg_color": theme.SURFACE_ALT, "text_color": theme.TEXT, "hover_color": theme.BORDER}
    return ctk.CTkButton(master, text=text, command=command, width=width, height=34, **colors, **kwargs)
