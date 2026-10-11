"""Dashboard: current scan results and historical statistics, clearly separated."""

from __future__ import annotations

import customtkinter as ctk

from smart_file_organizer.core.models import (
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components.widgets import (
    BarChart,
    Card,
    Column,
    FileTable,
    ScanBar,
    StatCard,
)
from smart_file_organizer.ui.pages.base import BasePage, muted_label
from smart_file_organizer.utils.formatting import format_size, format_timestamp


class DashboardPage(BasePage):
    title = "Dashboard"
    description = (
        "Current scan results appear first. Historical totals from recorded batches are shown separately below."
    )

    def build(self, body: ctk.CTkFrame) -> None:
        self._scan_bar = ScanBar(
            body,
            on_start=lambda folder, recursive: self.app.start_scan(folder, recursive),
            on_cancel=self.app.cancel_scan,
            default_recursive=self.ctx.settings.recursive_scan,
            initial_folder="",
        )
        self._scan_bar.grid(row=0, column=0, sticky="ew", pady=(0, 16))

        muted_label(body, "CURRENT SCAN").grid(row=1, column=0, sticky="w", pady=(0, 6))
        cards = ctk.CTkFrame(body, fg_color="transparent")
        cards.grid(row=2, column=0, sticky="ew")
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1)
        self._files = StatCard(cards, "Files scanned")
        self._storage = StatCard(cards, "Storage represented")
        self._categories = StatCard(cards, "Categories")
        self._largest = StatCard(cards, "Largest file")
        for index, card in enumerate((self._files, self._storage, self._categories, self._largest)):
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))

        self._empty = muted_label(body, "No scan yet. Choose a folder above to see real results.")
        self._empty.grid(row=3, column=0, sticky="w", pady=(10, 0))

        middle = ctk.CTkFrame(body, fg_color="transparent")
        middle.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        middle.grid_columnconfigure(0, weight=3)
        middle.grid_columnconfigure(1, weight=2)
        distribution = Card(middle)
        distribution.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        ctk.CTkLabel(
            distribution, text="Category distribution", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self._chart = BarChart(distribution)
        self._chart.pack(fill="x", padx=16, pady=(0, 14))
        largest = Card(middle)
        largest.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(
            largest, text="Largest files (current scan)", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self._largest_table = FileTable(
            largest,
            [Column("name", "Name", 180), Column("size", "Size", 90, "e")],
            height=7,
            selectmode="browse",
        )
        self._largest_table.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        muted_label(body, "HISTORY (RECORDED BATCHES, NOT CURRENT FILES)").grid(
            row=5, column=0, sticky="w", pady=(20, 6)
        )
        history_cards = ctk.CTkFrame(body, fg_color="transparent")
        history_cards.grid(row=6, column=0, sticky="ew")
        for column in range(5):
            history_cards.grid_columnconfigure(column, weight=1)
        self._batches = StatCard(history_cards, "Batches recorded")
        self._moved = StatCard(history_cards, "Files moved")
        self._skipped = StatCard(history_cards, "Skipped")
        self._failed = StatCard(history_cards, "Failed")
        self._held = StatCard(history_cards, "Size moved (not undone)")
        for index, card in enumerate((self._batches, self._moved, self._skipped, self._failed, self._held)):
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 8, 0))

        recent = Card(body)
        recent.grid(row=7, column=0, sticky="ew", pady=(16, 0))
        ctk.CTkLabel(recent, text="Recent batches", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 4)
        )
        self._recent = FileTable(
            recent,
            [
                Column("when", "When", 150),
                Column("kind", "Kind", 90),
                Column("folder", "Folder", 200),
                Column("status", "Status", 110),
                Column("summary", "Summary", 260),
            ],
            height=6,
            selectmode="browse",
        )
        self._recent.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def set_scan_running(self, running: bool) -> None:
        self._scan_bar.set_running(running)

    def refresh(self) -> None:
        scan = self.ctx.last_scan
        if scan is not None:
            self._scan_bar.set_folder(str(scan.root))
            self._empty.grid_remove()
            self._files.set_value(f"{scan.file_count:,}")
            self._storage.set_value(format_size(scan.total_bytes))
            self._categories.set_value(f"{len(scan.category_counts)}")
            largest = max(scan.entries, key=lambda entry: entry.size, default=None)
            self._largest.set_value(format_size(largest.size) if largest else "—")
            self._chart.set_data(list(scan.category_counts.items()))
            self._largest_table.clear()
            for entry in sorted(scan.entries, key=lambda e: -e.size)[:8]:
                self._largest_table.insert([entry.name, format_size(entry.size)], entry)
        else:
            self._empty.grid()
            for card in (self._files, self._storage, self._categories, self._largest):
                card.set_value("—")
            self._chart.set_data([])
            self._largest_table.clear()

        self._refresh_history()

    def _refresh_history(self) -> None:
        history = self.ctx.history
        totals = history.operation_totals()
        self._batches.set_value(f"{history.count_batches():,}")
        self._moved.set_value(f"{totals[STATUS_SUCCESS]:,}")
        self._skipped.set_value(f"{totals[STATUS_SKIPPED]:,}")
        self._failed.set_value(f"{totals[STATUS_FAILED]:,}")
        self._held.set_value(format_size(history.bytes_moved()))
        self._recent.clear()
        for batch in history.list_batches(limit=8):
            self._recent.insert(
                [
                    format_timestamp(batch.created_at),
                    "Preview" if batch.dry_run else batch.kind.title(),
                    batch.root_name,
                    batch.status.replace("_", " ").title(),
                    batch.note or f"{batch.operation_count} operation(s)",
                ],
                batch,
            )
