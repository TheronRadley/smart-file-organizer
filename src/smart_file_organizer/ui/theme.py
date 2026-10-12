"""Monochrome palette and ttk styling shared by every page.

Each colour is a ``(light, dark)`` pair so CustomTkinter can switch modes.
Accent colours are used only for status indicators.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

BG = ("#f3f3f3", "#121212")
SURFACE = ("#ffffff", "#1c1c1c")
SURFACE_ALT = ("#e9e9e9", "#242424")
BORDER = ("#d4d4d4", "#333333")
TEXT = ("#111111", "#f0f0f0")
MUTED = ("#5f5f5f", "#9a9a9a")
BUTTON = ("#2a2a2a", "#e6e6e6")
BUTTON_TEXT = ("#ffffff", "#111111")
BUTTON_HOVER = ("#444444", "#cfcfcf")
SUCCESS = ("#2e7d4f", "#5cc386")
WARNING = ("#9a6b00", "#e0b35a")
DANGER = ("#b03a3a", "#e27676")

FONT_FAMILY = "Segoe UI" if os.name == "nt" else "Helvetica"


def configure_appearance(theme: str) -> None:
    """Apply the light or dark appearance mode to CustomTkinter and ttk widgets."""
    ctk.set_appearance_mode("light" if theme == "light" else "dark")
    ctk.set_default_color_theme("blue")


def style_treeviews(widget: tk.Misc) -> None:
    """Give ttk Treeviews colours matching the current appearance mode."""
    dark = ctk.get_appearance_mode() == "Dark"
    background = "#1c1c1c" if dark else "#ffffff"
    foreground = "#f0f0f0" if dark else "#111111"
    heading = "#262626" if dark else "#e9e9e9"
    selected = "#3a3a3a" if dark else "#d9d9d9"
    style = ttk.Style(widget)
    style.theme_use("clam")
    style.configure(
        "Treeview",
        background=background,
        fieldbackground=background,
        foreground=foreground,
        bordercolor=background,
        rowheight=26,
        font=(FONT_FAMILY, 10),
    )
    style.configure(
        "Treeview.Heading",
        background=heading,
        foreground=foreground,
        relief="flat",
        font=(FONT_FAMILY, 10, "bold"),
    )
    style.map("Treeview", background=[("selected", selected)], foreground=[("selected", foreground)])
    style.map("Treeview.Heading", background=[("active", selected)])
    style.configure("Vertical.TScrollbar", background=heading, troughcolor=background, bordercolor=background)
