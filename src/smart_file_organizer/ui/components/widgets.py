"""Reusable widgets: page headers, cards, sortable file tables, bar charts and banners."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from tkinter import ttk
from typing import Any

import customtkinter as ctk

from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components.dialogs import choose_folder


class PageHeader(ctk.CTkFrame):
    """Page title and one-line description."""

    def __init__(self, master: Any, title: str, description: str) -> None:
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=24, weight="bold"), text_color=theme.TEXT).pack(anchor="w")
        ctk.CTkLabel(self, text=description, text_color=theme.MUTED, wraplength=820, justify="left").pack(
            anchor="w", pady=(2, 0)
        )


class Card(ctk.CTkFrame):
    """Bordered surface used to group related controls."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(
            master, fg_color=theme.SURFACE, border_width=1, border_color=theme.BORDER, corner_radius=10, **kwargs
        )


class StatCard(Card):
    """Single metric with a caption. ``value`` may be updated later."""

    def __init__(self, master: Any, caption: str, value: str = "—") -> None:
        super().__init__(master)
        self._value = ctk.CTkLabel(self, text=value, font=ctk.CTkFont(size=22, weight="bold"), text_color=theme.TEXT)
        self._value.pack(anchor="w", padx=16, pady=(12, 0))
        ctk.CTkLabel(self, text=caption, text_color=theme.MUTED).pack(anchor="w", padx=16, pady=(0, 12))

    def set_value(self, value: str) -> None:
        self._value.configure(text=value)


class Banner(ctk.CTkFrame):
    """Inline status message (warnings and errors) with a hidden default state."""

    def __init__(self, master: Any) -> None:
        super().__init__(master, fg_color=theme.SURFACE_ALT, corner_radius=8)
        self._label = ctk.CTkLabel(self, text="", text_color=theme.TEXT, wraplength=900, justify="left")
        self._label.pack(anchor="w", padx=12, pady=8)
        self.grid_remove()

    def show(self, message: str, color: tuple[str, str] = theme.WARNING) -> None:
        self._label.configure(text=message, text_color=color)
        self.grid()

    def hide(self) -> None:
        self.grid_remove()


@dataclass(frozen=True)
class Column:
    key: str
    heading: str
    width: int = 120
    anchor: str = "w"


class FileTable(ctk.CTkFrame):
    """ttk.Treeview with sorting, row objects and a consistent look.

    Each row can carry a Python object (``payload``) returned on selection, so
    pages never have to parse displayed text.
    """

    def __init__(
        self,
        master: Any,
        columns: Sequence[Column],
        *,
        height: int = 12,
        on_select: Callable[[], None] | None = None,
        on_activate: Callable[[Any], None] | None = None,
        selectmode: str = "extended",
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._columns = list(columns)
        self._payloads: dict[str, Any] = {}
        self._sort_state: tuple[str, bool] | None = None
        self._on_select = on_select
        self._on_activate = on_activate
        theme.style_treeviews(self)

        container = tk.Frame(self, bd=0, highlightthickness=0)
        container.pack(fill="both", expand=True)
        keys = [column.key for column in self._columns]
        self.tree = ttk.Treeview(container, columns=keys, show="tree headings", height=height, selectmode=selectmode)
        self.tree.column("#0", width=0, stretch=False)
        for column in self._columns:
            self.tree.heading(column.key, text=column.heading, command=lambda key=column.key: self.sort_by(key))
            self.tree.column(column.key, width=column.width, anchor=column.anchor, stretch=True)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", self._activated)
        self.tree.bind("<Return>", self._activated)

    def clear(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self._payloads.clear()

    def insert(self, values: Sequence[str], payload: Any = None, *, parent: str = "", tags: Sequence[str] = ()) -> str:
        iid = self.tree.insert(parent, "end", values=list(values), tags=tuple(tags), open=False)
        if payload is not None:
            self._payloads[iid] = payload
        return iid

    def items(self) -> list[tuple[str, Any]]:
        """(row id, payload) for every row that carries a payload, in display order."""
        return [(iid, self._payloads[iid]) for iid in self.tree.get_children("") if iid in self._payloads]

    def select_rows(self, iids: Sequence[str]) -> None:
        self.tree.selection_set(tuple(iids))

    def selected_payloads(self) -> list[Any]:
        return [self._payloads[iid] for iid in self.tree.selection() if iid in self._payloads]

    def selected_payload(self) -> Any | None:
        items = self.selected_payloads()
        return items[0] if items else None

    def set_tag_style(self, tag: str, color: tuple[str, str]) -> None:
        self.tree.tag_configure(tag, foreground=color[0] if ctk.get_appearance_mode() == "Light" else color[1])

    def sort_by(self, key: str) -> None:
        reverse = self._sort_state is not None and self._sort_state[0] == key and not self._sort_state[1]
        self._sort_state = (key, reverse)
        index = [column.key for column in self._columns].index(key)
        children = list(self.tree.get_children(""))
        children.sort(key=lambda iid: _sort_value(self.tree.item(iid, "values")[index]), reverse=reverse)
        for position, iid in enumerate(children):
            self.tree.move(iid, "", position)

    def _selection_changed(self, _event: object) -> None:
        if self._on_select is not None:
            self._on_select()

    def _activated(self, _event: object) -> None:
        if self._on_activate is not None:
            payload = self.selected_payload()
            if payload is not None:
                self._on_activate(payload)


def _sort_value(text: str) -> tuple[int, Any]:
    """Sort numbers numerically (e.g. '1.5 MB' or '2048') and everything else case-insensitively."""
    cleaned = str(text).replace(",", "")
    try:
        return (0, float(cleaned.split(" ")[0]) * _unit_multiplier(cleaned))
    except ValueError:
        return (1, cleaned.casefold())


def _unit_multiplier(text: str) -> float:
    units = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    for unit, multiplier in units.items():
        if text.endswith(unit):
            return float(multiplier)
    return 1.0


class BarChart(ctk.CTkFrame):
    """Simple horizontal bar chart drawn on a canvas (category distribution)."""

    def __init__(self, master: Any, height: int = 220) -> None:
        super().__init__(master, fg_color="transparent")
        self._canvas = tk.Canvas(self, height=height, highlightthickness=0, borderwidth=0)
        self._canvas.pack(fill="x", expand=True)
        self._canvas.bind("<Configure>", lambda _e: self._redraw())
        self._data: list[tuple[str, int]] = []

    def set_data(self, data: Sequence[tuple[str, int]]) -> None:
        self._data = sorted(data, key=lambda item: -item[1])
        self._redraw()

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        dark = ctk.get_appearance_mode() == "Dark"
        text = "#f0f0f0" if dark else "#111111"
        muted = "#9a9a9a" if dark else "#5f5f5f"
        bar = "#e6e6e6" if dark else "#2a2a2a"
        width = max(canvas.winfo_width(), 300)
        if not self._data:
            canvas.create_text(12, 20, anchor="w", fill=muted, text="No categories yet — run a scan first.")
            return
        label_width = 140
        count_width = 90
        bar_width = max(width - label_width - count_width - 24, 60)
        peak = max(count for _, count in self._data) or 1
        row_height = 28
        for row, (name, count) in enumerate(self._data[:12]):
            y = 12 + row * row_height
            canvas.create_text(12, y + 9, anchor="w", fill=text, text=name)
            length = max(2, int(bar_width * count / peak))
            canvas.create_rectangle(label_width, y + 2, label_width + length, y + 18, fill=bar, outline="")
            canvas.create_text(label_width + bar_width + 12, y + 9, anchor="w", fill=muted, text=f"{count:,}")
        canvas.configure(height=max(60, 24 + row_height * min(len(self._data), 12)))


class FolderPicker(ctk.CTkFrame):
    """Read-only path display with a native "Browse" button."""

    def __init__(self, master: Any, on_browse: Callable[[], None], placeholder: str = "Choose a folder…") -> None:
        super().__init__(master, fg_color="transparent")
        self._entry = ctk.CTkEntry(self, placeholder_text=placeholder, height=34)
        self._entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            self,
            text="Browse…",
            width=110,
            height=34,
            command=on_browse,
            fg_color=theme.BUTTON,
            text_color=theme.BUTTON_TEXT,
            hover_color=theme.BUTTON_HOVER,
        ).pack(side="left", padx=(8, 0))

    def get(self) -> str:
        return self._entry.get().strip()

    def set(self, path: str) -> None:
        self._entry.delete(0, "end")
        self._entry.insert(0, path)


class ScanBar(Card):
    """Folder selection, subfolder option, start/cancel buttons and progress for a scan."""

    def __init__(
        self,
        master: Any,
        *,
        on_start: Callable[[str, bool], None],
        on_cancel: Callable[[], None],
        start_text: str = "Scan folder",
        default_recursive: bool = False,
        initial_folder: str = "",
    ) -> None:
        super().__init__(master)
        self.grid_columnconfigure(0, weight=1)
        self._picker = FolderPicker(self, on_browse=self._browse)
        self._picker.grid(row=0, column=0, columnspan=4, sticky="ew", padx=16, pady=(14, 8))
        if initial_folder:
            self._picker.set(initial_folder)
        self._recursive = ctk.CTkCheckBox(self, text="Include subfolders", text_color=theme.TEXT)
        self._recursive.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))
        if default_recursive:
            self._recursive.select()
        self._start = ctk.CTkButton(
            self,
            text=start_text,
            width=150,
            height=34,
            command=lambda: on_start(self._picker.get(), bool(self._recursive.get())),
            fg_color=theme.BUTTON,
            text_color=theme.BUTTON_TEXT,
            hover_color=theme.BUTTON_HOVER,
        )
        self._start.grid(row=1, column=1, padx=(8, 8), pady=(0, 12), sticky="e")
        self._cancel = ctk.CTkButton(
            self,
            text="Cancel",
            width=110,
            height=34,
            command=on_cancel,
            fg_color=theme.SURFACE_ALT,
            text_color=theme.TEXT,
            hover_color=theme.BORDER,
            state="disabled",
        )
        self._cancel.grid(row=1, column=2, padx=(0, 8), pady=(0, 12), sticky="e")
        self._status = ctk.CTkLabel(self, text="", text_color=theme.MUTED, anchor="w")
        self._status.grid(row=2, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 4))
        self._progress = ctk.CTkProgressBar(self, height=6)
        self._progress.grid(row=3, column=0, columnspan=4, sticky="ew", padx=16, pady=(0, 14))
        self._progress.set(0)
        self._current_folder = ""

    @property
    def folder(self) -> str:
        return self._picker.get()

    def set_folder(self, folder: str) -> None:
        self._picker.set(folder)

    def set_running(self, running: bool) -> None:
        self._start.configure(state="disabled" if running else "normal")
        self._cancel.configure(state="normal" if running else "disabled")
        if running:
            self._progress.configure(mode="indeterminate")
            self._progress.start()
        else:
            self._progress.stop()
            self._progress.configure(mode="determinate")
            self._progress.set(0)

    def set_status(self, text: str) -> None:
        self._status.configure(text=text)

    def _browse(self) -> None:
        chosen = choose_folder(self.winfo_toplevel())
        if chosen:
            self._picker.set(chosen)
