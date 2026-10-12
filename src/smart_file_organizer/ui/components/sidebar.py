"""Left navigation rail."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import customtkinter as ctk

from smart_file_organizer import __version__
from smart_file_organizer.ui import theme


class Sidebar(ctk.CTkFrame):
    """Vertical navigation with one highlighted active item."""

    def __init__(self, master: Any, items: Sequence[tuple[str, str]], on_select: Callable[[str], None]) -> None:
        super().__init__(master, width=210, corner_radius=0, fg_color=theme.SURFACE, border_width=0)
        self.grid_propagate(False)
        ctk.CTkLabel(
            self,
            text="Smart File\nOrganizer",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=theme.TEXT,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(24, 20))
        self._buttons: dict[str, ctk.CTkButton] = {}
        for key, label in items:
            button = ctk.CTkButton(
                self,
                text=label,
                anchor="w",
                height=38,
                corner_radius=8,
                fg_color="transparent",
                text_color=theme.TEXT,
                hover_color=theme.SURFACE_ALT,
                command=lambda name=key: on_select(name),
            )
            button.pack(fill="x", padx=12, pady=2)
            self._buttons[key] = button
        ctk.CTkLabel(self, text=f"Version {__version__}", text_color=theme.MUTED, font=ctk.CTkFont(size=11)).pack(
            side="bottom", anchor="w", padx=20, pady=16
        )

    def set_active(self, key: str) -> None:
        for name, button in self._buttons.items():
            active = name == key
            button.configure(
                fg_color=theme.SURFACE_ALT if active else "transparent",
                font=ctk.CTkFont(weight="bold" if active else "normal"),
            )
