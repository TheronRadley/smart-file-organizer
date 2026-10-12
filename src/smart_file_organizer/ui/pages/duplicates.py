"""Duplicate Finder: groups files with identical contents. Never deletes anything."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from smart_file_organizer.core.duplicates import (
    DuplicateFile,
    DuplicateReport,
    find_duplicates,
)
from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.tasks import TaskContext
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
    validate_folder,
)
from smart_file_organizer.utils.formatting import format_ns_timestamp, format_size
from smart_file_organizer.utils.system import open_in_file_manager

DUPLICATES_KEY = "duplicates"


class DuplicatesPage(BasePage):
    title = "Duplicate Finder"
    description = (
        "Finds files with identical contents: sizes narrow the search, then SHA-256 hashes confirm matches. "
        "Names are never used to decide. Symbolic links are skipped. Nothing is deleted here; "
        "mark files for your own review instead."
    )
    scrollable = False

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_rowconfigure(3, weight=1)
        self._scan_bar = ScanBar(
            body,
            on_start=self._start,
            on_cancel=lambda: self.runner.cancel(DUPLICATES_KEY),
            start_text="Find duplicates",
            default_recursive=True,
            initial_folder="",
        )
        self._scan_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        options = ctk.CTkFrame(body, fg_color="transparent")
        options.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        muted_label(options, "Minimum size (KB):").pack(side="left")
        self._min_kb = ctk.CTkEntry(options, width=90, height=32)
        self._min_kb.insert(0, "1")
        self._min_kb.pack(side="left", padx=8)
        self._summary = muted_label(options, "No duplicate search run yet.")
        self._summary.pack(side="left", padx=16)

        self._banner = Banner(body)
        self._banner.grid(row=2, column=0, sticky="ew", pady=(0, 8))

        results = Card(body)
        results.grid(row=3, column=0, sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(1, weight=1)
        toolbar = ctk.CTkFrame(results, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        button(toolbar, "Open file location", self._open_location, width=170).pack(side="left")
        button(toolbar, "Mark / unmark for review", self._toggle_review, width=200).pack(side="left", padx=8)
        button(toolbar, "Copy review list", self._copy_review_list, width=150).pack(side="left")
        self._review_label = muted_label(toolbar, "")
        self._review_label.pack(side="right")
        self._table = FileTable(
            results,
            [
                Column("name", "Group / file", 280),
                Column("location", "Location", 320),
                Column("size", "Size", 90, "e"),
                Column("modified", "Modified", 130),
                Column("note", "Note", 200),
            ],
            on_activate=lambda dup: open_in_file_manager(dup.path),
            selectmode="browse",
        )
        self._table.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        self._table.set_tag_style("review", theme.WARNING)
        self._review: set[Path] = set()
        self._shown_report: DuplicateReport | None = None

    def set_scan_running(self, running: bool) -> None:
        self._scan_bar.set_running(running)

    def _start(self, folder: str, recursive: bool) -> None:
        try:
            path = validate_folder(folder)
            min_kb = parse_positive_float(self._min_kb.get(), "Minimum size", minimum=0.0)
        except ValueError as exc:
            dialogs.error(self, "Check the inputs", str(exc))
            return
        min_size = max(1, int(min_kb * 1024))
        settings = self.ctx.settings
        options = ScanOptions(
            recursive=recursive,
            excluded_folders=tuple(settings.excluded_folders),
            excluded_patterns=tuple(settings.excluded_patterns),
        )

        def work(task: TaskContext) -> DuplicateReport:
            return find_duplicates(
                path,
                options,
                min_size=min_size,
                progress=lambda done, total: task.report(f"Comparing candidates: {done:,} of {total:,} files…"),
                cancel_event=task.cancel_event,
            )

        def success(report: DuplicateReport) -> None:
            self._scan_bar.set_running(False)
            self.ctx.last_duplicates = report
            present = {file.path for group in report.groups for file in group.files}
            self._review &= present
            self._render(report)
            self.notify(
                "Duplicate search cancelled." if report.cancelled else f"Found {len(report.groups)} duplicate group(s)."
            )

        def failure(message: str) -> None:
            self._scan_bar.set_running(False)
            dialogs.error(self, "Duplicate search failed", message)

        if self.runner.submit(DUPLICATES_KEY, work, on_success=success, on_error=failure, on_progress=self.notify):
            self._scan_bar.set_running(True)

    def _render(self, report: DuplicateReport) -> None:
        self._table.clear()
        self._shown_report = report
        if report.cancelled:
            self._banner.show("The search was cancelled, so the groups below may be incomplete.")
        elif report.issues:
            self._banner.show(
                f"{len(report.issues)} item(s) could not be compared (access errors or files changed while being read)."
            )
        else:
            self._banner.hide()
        for index, group in enumerate(report.groups, start=1):
            parent = self._table.insert(
                [
                    f"Group {index} · {len(group.files)} files",
                    "",
                    format_size(group.size),
                    "",
                    f"Recoverable {format_size(group.recoverable_bytes)}",
                ],
                None,
            )
            for duplicate in group.files:
                shared = (
                    duplicate.file_id is not None and sum(1 for f in group.files if f.file_id == duplicate.file_id) > 1
                )
                note = "Hard link (same storage)" if shared else ""
                marker = "★ " if duplicate.path in self._review else ""
                self._table.insert(
                    [
                        marker + duplicate.path.name,
                        str(duplicate.path.parent),
                        format_size(duplicate.size),
                        format_ns_timestamp(duplicate.modified_ns),
                        note,
                    ],
                    duplicate,
                    parent=parent,
                    tags=("review",) if duplicate.path in self._review else (),
                )
        self._summary.configure(
            text=(
                f"{len(report.groups):,} group(s) · {report.duplicate_file_count:,} files · "
                f"recoverable up to {format_size(report.recoverable_bytes)} (nothing deleted)"
            )
        )
        self._review_label.configure(text=f"{len(self._review)} marked for review")

    def _selected_file(self) -> DuplicateFile | None:
        item = self._table.selected_payload()
        if item is None:
            dialogs.info(self, "Select a file", "Select a file row (not a group heading).")
        return item

    def _open_location(self) -> None:
        duplicate = self._selected_file()
        if duplicate is None:
            return
        try:
            open_in_file_manager(duplicate.path.parent)
        except OSError as exc:
            dialogs.error(self, "Could not open folder", str(exc))

    def _toggle_review(self) -> None:
        duplicate = self._selected_file()
        if duplicate is None:
            return
        if duplicate.path in self._review:
            self._review.discard(duplicate.path)
        else:
            self._review.add(duplicate.path)
        if self._shown_report is not None:
            self._render(self._shown_report)

    def _copy_review_list(self) -> None:
        if not self._review:
            dialogs.info(self, "Nothing marked", "Mark some files for review first.")
            return
        text = "\n".join(sorted(str(path) for path in self._review))
        self.clipboard_clear()
        self.clipboard_append(text)
        self.notify(f"Copied {len(self._review)} path(s) to the clipboard.")

    def refresh(self) -> None:
        if self.ctx.last_duplicates is not None and self._shown_report is None:
            self._render(self.ctx.last_duplicates)
