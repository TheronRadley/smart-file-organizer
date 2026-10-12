"""Large Files: largest files from the current scan, with filters and access-error warnings."""

from __future__ import annotations

import customtkinter as ctk

from smart_file_organizer.core.large_files import LargeFileReport, find_large_files
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.widgets import (
    Banner,
    Card,
    Column,
    FileTable,
    ScanBar,
)
from smart_file_organizer.ui.pages.base import (
    BasePage,
    button,
    muted_label,
    parse_positive_float,
)
from smart_file_organizer.utils.formatting import format_ns_timestamp, format_size
from smart_file_organizer.utils.system import open_in_file_manager

MAX_ROWS = 2000


class LargeFilesPage(BasePage):
    title = "Large Files"
    description = (
        "Largest files in the current scan, biggest first. Nothing is deleted automatically; open the folder to decide."
    )
    scrollable = False

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_rowconfigure(3, weight=1)
        self._scan_bar = ScanBar(
            body,
            on_start=lambda folder, recursive: self.app.start_scan(folder, recursive),
            on_cancel=self.app.cancel_scan,
            start_text="Scan folder",
            default_recursive=True,
        )
        self._scan_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        filters = ctk.CTkFrame(body, fg_color="transparent")
        filters.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        muted_label(filters, "Minimum size (MB):").pack(side="left")
        self._min_mb = ctk.CTkEntry(filters, width=90, height=32)
        self._min_mb.insert(0, f"{self.ctx.settings.large_file_min_mb:g}")
        self._min_mb.pack(side="left", padx=8)
        muted_label(filters, "Extensions (optional):").pack(side="left", padx=(12, 0))
        self._extensions = ctk.CTkEntry(filters, width=200, height=32, placeholder_text="e.g. mkv, iso, zip")
        self._extensions.pack(side="left", padx=8)
        button(filters, "Apply filters", self.refresh, width=130).pack(side="left")
        self._count = muted_label(filters, "")
        self._count.pack(side="left", padx=14)

        self._banner = Banner(body)
        self._banner.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        results = Card(body)
        results.grid(row=3, column=0, sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(results, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        button(toolbar, "Open containing folder", self._open_folder, width=200).pack(side="left")
        self._table = FileTable(
            results,
            [
                Column("name", "Name", 260),
                Column("size", "Size", 100, "e"),
                Column("ext", "Ext", 70),
                Column("modified", "Modified", 140),
                Column("location", "Location", 320),
            ],
            on_activate=lambda entry: open_in_file_manager(entry.path.parent),
            selectmode="browse",
        )
        self._table.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    def set_scan_running(self, running: bool) -> None:
        self._scan_bar.set_running(running)

    def refresh(self) -> None:
        scan = self.ctx.last_scan
        self._table.clear()
        if scan is None:
            self._banner.hide()
            self._count.configure(text="Scan a folder to list its largest files.")
            return
        self._scan_bar.set_folder(str(scan.root))
        try:
            min_mb = parse_positive_float(self._min_mb.get(), "Minimum size", minimum=0.0)
        except ValueError as exc:
            dialogs.error(self, "Check the filter", str(exc))
            return
        extensions = [part for part in self._extensions.get().split(",") if part.strip()]
        report: LargeFileReport = find_large_files(
            scan,
            min_bytes=int(min_mb * 1024 * 1024),
            extensions=extensions,
            limit=MAX_ROWS,
        )
        self.ctx.last_large = report
        if report.incomplete:
            reason = "The scan was cancelled" if report.cancelled else f"{len(report.issues)} item(s) could not be read"
            self._banner.show(f"Results are incomplete: {reason}. Files that could not be read are not listed.")
        else:
            self._banner.hide()
        for entry in report.files:
            location = str(entry.path.parent)
            self._table.insert(
                [
                    entry.name,
                    format_size(entry.size),
                    entry.extension or "—",
                    format_ns_timestamp(entry.modified_ns),
                    location,
                ],
                entry,
            )
        self._count.configure(text=f"{len(report.files):,} file(s) at least {min_mb:g} MB")

    def _open_folder(self) -> None:
        entry = self._table.selected_payload()
        if entry is None:
            dialogs.info(self, "Select a file", "Select a file first.")
            return
        try:
            open_in_file_manager(entry.path.parent)
        except OSError as exc:
            dialogs.error(self, "Could not open folder", str(exc))
