"""About: what the application does, what it does not do, and where data lives."""

from __future__ import annotations

import customtkinter as ctk

from smart_file_organizer import __version__
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components.widgets import Card
from smart_file_organizer.ui.pages.base import BasePage, muted_label

PRIVACY = (
    "Smart File Organizer works entirely on this computer. It makes no network connections, sends no telemetry, "
    "uploads nothing and never reads file contents for classification. Settings and history are stored only in "
    "your user data folder."
)
LIMITS = (
    "• Classification uses file extensions only; a misnamed file is organized by its extension.\n"
    "• Moves are applied one file at a time. The filesystem offers no multi-file transaction, so a batch can "
    "end partly applied; "
    "History shows exactly which files moved.\n"
    "• Undo is refused when a file was edited, replaced, or its original location is occupied.\n"
    "• Monitoring polls for changes (default every 5 seconds); it does not run when the app is closed.\n"
    "• Duplicate detection only reports copies. Deleting anything is left to you."
)


class AboutPage(BasePage):
    title = "About"
    description = "Smart File Organizer is an open-source desktop tool for safe, previewable file organization."

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_columnconfigure(0, weight=1)
        info = Card(body)
        info.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(
            info, text=f"Version {__version__}", text_color=theme.TEXT, font=ctk.CTkFont(size=16, weight="bold")
        ).pack(anchor="w", padx=16, pady=(14, 4))
        muted_label(
            info, "Licensed under the MIT License. See LICENSE in the project repository.", wraplength=900
        ).pack(anchor="w", padx=16, pady=(0, 14))

        privacy = Card(body)
        privacy.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(privacy, text="Privacy", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        muted_label(privacy, PRIVACY, wraplength=900).pack(anchor="w", padx=16, pady=(0, 14))

        limits = Card(body)
        limits.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(limits, text="Known limitations", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        muted_label(limits, LIMITS, wraplength=900).pack(anchor="w", padx=16, pady=(0, 14))

        docs = Card(body)
        docs.grid(row=3, column=0, sticky="ew")
        ctk.CTkLabel(docs, text="Documentation", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(14, 4)
        )
        muted_label(
            docs,
            "docs/USER_GUIDE.md - step-by-step usage\n"
            "docs/SAFETY.md - guarantees and what to do after a partial failure\n"
            "docs/ARCHITECTURE.md - how the code is organized",
            wraplength=900,
        ).pack(anchor="w", padx=16, pady=(0, 14))
