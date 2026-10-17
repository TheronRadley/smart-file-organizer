"""Organize Files: scan, filter results, preview the plan, dry-run and apply."""

from __future__ import annotations

from typing import Any

import customtkinter as ctk

from smart_file_organizer.config import (
    COLLISION_RENAME,
    COLLISION_SKIP,
    ORGANIZE_BY_DATE,
    ORGANIZE_BY_EXTENSION,
    ORGANIZE_BY_TYPE,
)
from smart_file_organizer.core.export import ExportError, scan_export, write_export
from smart_file_organizer.core.metadata import read_metadata
from smart_file_organizer.core.models import (
    STATUS_FAILED,
    STATUS_READY,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_WOULD_MOVE,
    BatchOutcome,
    FileEntry,
    OrganizePlan,
    PlannedOperation,
    ScanResult,
)
from smart_file_organizer.core.organizer import build_plan, execute_plan
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
from smart_file_organizer.ui.pages.base import BasePage, button, muted_label
from smart_file_organizer.utils.formatting import (
    format_ns_timestamp,
    format_size,
    format_timestamp,
)
from smart_file_organizer.utils.system import open_in_file_manager

APPLY_KEY = "apply"
MODE_LABELS = {
    "By file type": ORGANIZE_BY_TYPE,
    "By extension": ORGANIZE_BY_EXTENSION,
    "By modified month": ORGANIZE_BY_DATE,
}
COLLISION_LABELS = {
    "Rename safely (name (2).ext)": COLLISION_RENAME,
    "Skip conflicting files": COLLISION_SKIP,
}
SORT_OPTIONS = ("Name", "Size (largest first)", "Type", "Modified (newest first)")
MAX_ROWS = 3000


class OrganizePage(BasePage):
    title = "Organize Files"
    description = (
        "Scan a folder, review the proposed moves, dry-run them, then apply the ones you approve. "
        "Nothing moves until you confirm. Every move is recorded and can be undone from History."
    )
    scrollable = False

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_rowconfigure(2, weight=1)
        self._scan_bar = ScanBar(
            body,
            on_start=lambda folder, recursive: self.app.start_scan(folder, recursive),
            on_cancel=self.app.cancel_scan,
            default_recursive=self.ctx.settings.recursive_scan,
        )
        self._scan_bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self._banner = Banner(body)
        self._banner.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        self._tabs = ctk.CTkTabview(
            body,
            fg_color=theme.SURFACE,
            segmented_button_selected_color=theme.BUTTON,
            segmented_button_selected_hover_color=theme.BUTTON_HOVER,
            segmented_button_unselected_color=theme.SURFACE_ALT,
        )
        self._tabs.grid(row=2, column=0, sticky="nsew")
        files_tab = self._tabs.add("Files")
        preview_tab = self._tabs.add("Preview & apply")
        self._build_files_tab(files_tab)
        self._build_preview_tab(preview_tab)

    # ----- files tab -----------------------------------------------------

    def _build_files_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        filters = ctk.CTkFrame(tab, fg_color="transparent")
        filters.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        self._search = ctk.CTkEntry(filters, placeholder_text="Search file names", width=220, height=32)
        self._search.pack(side="left")
        self._category = ctk.CTkOptionMenu(
            filters, values=["All categories"], width=170, height=32, command=lambda _v: self._apply_filters()
        )
        self._category.pack(side="left", padx=8)
        self._extension = ctk.CTkEntry(filters, placeholder_text="Extension, e.g. pdf", width=150, height=32)
        self._extension.pack(side="left")
        self._min_mb = ctk.CTkEntry(filters, placeholder_text="Min MB", width=80, height=32)
        self._min_mb.pack(side="left", padx=(8, 4))
        self._max_mb = ctk.CTkEntry(filters, placeholder_text="Max MB", width=80, height=32)
        self._max_mb.pack(side="left")
        self._sort = ctk.CTkOptionMenu(
            filters, values=list(SORT_OPTIONS), width=190, height=32, command=lambda _v: self._apply_filters()
        )
        self._sort.pack(side="left", padx=8)
        button(filters, "Apply filters", self._apply_filters, width=120).pack(side="left")
        button(filters, "Export scan…", self._export, width=130).pack(side="left", padx=8)
        self._search.bind("<Return>", lambda _e: self._apply_filters())
        self._extension.bind("<Return>", lambda _e: self._apply_filters())

        split = ctk.CTkFrame(tab, fg_color="transparent")
        split.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 6))
        split.grid_columnconfigure(0, weight=3)
        split.grid_columnconfigure(1, weight=2)
        split.grid_rowconfigure(1, weight=1)
        self._count = muted_label(split, "")
        self._count.grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._files = FileTable(
            split,
            [
                Column("name", "Name", 220),
                Column("type", "Category", 110),
                Column("size", "Size", 90, "e"),
                Column("modified", "Modified", 130),
                Column("location", "Folder", 220),
            ],
            on_select=self._show_details,
            on_activate=lambda entry: open_in_file_manager(entry.path),
            selectmode="browse",
        )
        self._files.grid(row=1, column=0, sticky="nsew", padx=(0, 8))

        details = Card(split)
        details.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(8, 0))
        ctk.CTkLabel(details, text="Details", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 6)
        )
        self._details = muted_label(details, "Select a file to see its metadata.", wraplength=300)
        self._details.pack(anchor="w", padx=16, fill="x")
        button(details, "Open containing folder", self._open_selected_folder, width=200).pack(
            anchor="w", padx=16, pady=12
        )

    def _export(self) -> None:
        """Write the current scan result to a CSV or JSON report."""
        scan = self.ctx.last_scan
        if scan is None:
            dialogs.info(self, "Nothing to export", "Scan a folder first.")
            return
        path = dialogs.choose_save_path(self, "Export scan results", "scan.csv")
        if not path:
            return
        try:
            written = write_export(scan_export(scan), path)
        except ExportError as exc:
            dialogs.error(self, "Could not export", str(exc))
            return
        self.notify(f"Scan exported to {written}")

    def _apply_filters(self) -> None:
        self._files.clear()
        scan = self.ctx.last_scan
        if scan is None:
            self._count.configure(text="No scan yet.")
            return
        entries = self._filtered_entries(scan)
        for entry in entries[:MAX_ROWS]:
            self._files.insert(
                [
                    entry.name,
                    entry.category,
                    format_size(entry.size),
                    format_ns_timestamp(entry.modified_ns),
                    str(entry.path.parent.relative_to(scan.root)) if entry.path.parent != scan.root else "(top level)",
                ],
                entry,
            )
        shown = min(len(entries), MAX_ROWS)
        note = f" (showing first {MAX_ROWS:,})" if len(entries) > MAX_ROWS else ""
        self._count.configure(text=f"{shown:,} of {scan.file_count:,} files shown{note}")

    def _filtered_entries(self, scan: ScanResult) -> list[FileEntry]:
        query = self._search.get().strip().casefold()
        category = self._category.get()
        extension = self._extension.get().strip().lower().lstrip(".")
        try:
            min_bytes = int(float(self._min_mb.get() or 0) * 1024 * 1024)
            max_bytes = int(float(self._max_mb.get()) * 1024 * 1024) if self._max_mb.get().strip() else None
        except ValueError:
            self.notify("Size filters must be numbers (megabytes).")
            min_bytes, max_bytes = 0, None
        entries = [
            entry
            for entry in scan.entries
            if (not query or query in entry.name.casefold())
            and (category == "All categories" or entry.category == category)
            and (not extension or entry.extension == extension)
            and entry.size >= min_bytes
            and (max_bytes is None or entry.size <= max_bytes)
        ]
        sort = self._sort.get()
        if sort == "Size (largest first)":
            entries.sort(key=lambda e: -e.size)
        elif sort == "Type":
            entries.sort(key=lambda e: (e.category, e.name.casefold()))
        elif sort == "Modified (newest first)":
            entries.sort(key=lambda e: -e.modified_ns)
        else:
            entries.sort(key=lambda e: e.name.casefold())
        return entries

    def _show_details(self) -> None:
        entry = self._files.selected_payload()
        if entry is None:
            return
        metadata = read_metadata(entry)
        dimensions = f"{metadata.dimensions[0]} × {metadata.dimensions[1]} px" if metadata.dimensions else "—"
        lines = [
            f"Name: {metadata.name}",
            f"Extension: {metadata.extension or '—'}",
            f"Size: {format_size(metadata.size)} ({metadata.size:,} bytes)",
            f"Created: {format_timestamp(metadata.created_ts)}",
            f"Modified: {format_ns_timestamp(metadata.modified_ns)}",
            f"Location: {metadata.location}",
            f"Detected category: {metadata.category}",
            f"Dimensions: {dimensions}",
        ]
        self._details.configure(text="\n".join(lines))

    def _open_selected_folder(self) -> None:
        entry = self._files.selected_payload()
        if entry is None:
            dialogs.info(self, "Select a file", "Select a file first.")
            return
        try:
            open_in_file_manager(entry.path.parent)
        except OSError as exc:
            dialogs.error(self, "Could not open folder", str(exc))

    # ----- preview tab ---------------------------------------------------

    def _build_preview_tab(self, tab: ctk.CTkFrame) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        controls = ctk.CTkFrame(tab, fg_color="transparent")
        controls.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        self._mode = ctk.CTkOptionMenu(controls, values=list(MODE_LABELS), width=190, height=34)
        self._mode.set("By file type")
        self._mode.pack(side="left")
        self._collision = ctk.CTkOptionMenu(controls, values=list(COLLISION_LABELS), width=230, height=34)
        self._collision.set("Rename safely (name (2).ext)")
        self._collision.pack(side="left", padx=8)
        button(controls, "Build preview", self._build_preview, primary=True, width=130).pack(side="left")
        button(controls, "Dry run", self._dry_run, width=110).pack(side="left", padx=8)
        button(controls, "Select all ready", self._select_all_ready, width=140).pack(side="left")
        self._apply_button = button(controls, "Apply selected…", self._apply, primary=True, width=160)
        self._apply_button.pack(side="right")

        self._summary = muted_label(tab, "Build a preview from a scanned folder to see proposed moves.")
        self._summary.grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))
        self._plan_table = FileTable(
            tab,
            [
                Column("source", "Source (relative to folder)", 300),
                Column("target", "Destination folder / name", 300),
                Column("size", "Size", 90, "e"),
                Column("status", "Status / reason", 340),
            ],
            selectmode="extended",
        )
        self._plan_table.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
        self._plan_table.set_tag_style("ok", theme.SUCCESS)
        self._plan_table.set_tag_style("bad", theme.WARNING)
        self._plan_table.set_tag_style("err", theme.DANGER)
        self._log = ctk.CTkTextbox(tab, height=110, fg_color=theme.SURFACE_ALT, text_color=theme.TEXT)
        self._log.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        self._log.insert("end", "Results of dry runs and applied batches appear here.\n")
        self._log.configure(state="disabled")

    def _current_plan(self) -> OrganizePlan | None:
        return self.ctx.last_plan

    def _build_preview(self) -> None:
        scan = self.ctx.last_scan
        if scan is None:
            dialogs.info(self, "No scan yet", "Scan a folder on the Files tab first.")
            return
        if scan.cancelled:
            dialogs.info(
                self, "Incomplete scan", "The last scan was cancelled. Scan the folder again for a complete preview."
            )
            return
        plan = build_plan(
            scan,
            self.ctx.classifier,
            organize_by=MODE_LABELS[self._mode.get()],
            collision_strategy=COLLISION_LABELS[self._collision.get()],
            disabled_categories=set(self.ctx.settings.disabled_categories),
        )
        self.ctx.last_plan = plan
        self._render_plan(plan)

    def _render_plan(self, plan: OrganizePlan | None, outcome: BatchOutcome | None = None) -> None:
        self._plan_table.clear()
        if plan is None:
            self._summary.configure(text="Build a preview from a scanned folder to see proposed moves.")
            return
        outcomes = {item.index: item for item in outcome.outcomes} if outcome else {}
        ready_iids: list[str] = []
        for operation in plan.operations:
            status_text, tag = self._status_for(operation, outcomes.get(operation.index))
            target = (
                f"{operation.destination_folder}/{operation.destination.name}"
                if operation.destination is not None and operation.destination_folder
                else "—"
            )
            iid = self._plan_table.insert(
                [operation.source_relative, target, format_size(operation.size), status_text],
                operation,
                tags=(tag,) if tag else (),
            )
            if operation.status == STATUS_READY:
                ready_iids.append(iid)
        self._plan_table.select_rows(ready_iids)
        self._summary.configure(
            text=(
                f"{len(plan.ready):,} ready to move ({format_size(plan.bytes_to_move)}) · "
                f"{len(plan.skipped):,} skipped · mode: {plan.organize_by} · "
                f"collisions: {plan.collision_strategy}"
            )
        )

    @staticmethod
    def _status_for(operation: PlannedOperation, result: Any) -> tuple[str, str]:
        if result is not None:
            if result.status == STATUS_SUCCESS:
                return "Moved", "ok"
            if result.status == STATUS_WOULD_MOVE:
                return "Would move (dry run)", "ok"
            if result.status == STATUS_FAILED:
                return f"Failed: {result.reason}", "err"
            return f"Skipped: {result.reason}", "bad"
        if operation.status == STATUS_READY:
            return "Ready", "ok"
        return f"Skipped: {operation.reason}", "bad"

    def _select_all_ready(self) -> None:
        self._plan_table.select_rows([iid for iid, op in self._plan_table.items() if op.status == STATUS_READY])

    def _selected_ready(self) -> list[PlannedOperation]:
        return [op for op in self._plan_table.selected_payloads() if op.status == STATUS_READY]

    def _dry_run(self) -> None:
        plan = self._current_plan()
        if plan is None:
            dialogs.info(self, "Build a preview first", "Build a preview before running a dry run.")
            return
        indices = [op.index for op in self._selected_ready()]
        if not indices:
            dialogs.info(self, "Nothing selected", "Select at least one ready file in the table.")
            return

        def work(task: TaskContext) -> BatchOutcome:
            return execute_plan(plan, history=None, selected=indices, dry_run=True, cancel_event=task.cancel_event)

        self.runner.submit(
            "dry-run",
            work,
            on_success=lambda outcome: self._show_outcome(plan, outcome, "Dry run complete — nothing was changed."),
            on_error=lambda message: dialogs.error(self, "Dry run failed", message),
        )

    def _apply(self) -> None:
        plan = self._current_plan()
        if plan is None:
            dialogs.info(self, "Build a preview first", "Build a preview before applying changes.")
            return
        selected = self._selected_ready()
        if not selected:
            dialogs.info(self, "Nothing selected", "Select at least one ready file in the table.")
            return
        size = format_size(sum(op.size for op in selected))
        if not dialogs.confirm(
            self,
            "Apply organization",
            f"Move {len(selected):,} file(s) ({size}) into their folders under\n{plan.root}?\n\n"
            "Each move is recorded in History and can be undone when it is still safe to do so.",
        ):
            return
        indices = [op.index for op in selected]
        root = plan.root

        def work(task: TaskContext) -> BatchOutcome:
            return execute_plan(
                plan,
                history=self.ctx.history,
                selected=indices,
                cancel_event=task.cancel_event,
                progress=lambda done, total: task.report(f"Moving {done:,} of {total:,}…"),
            )

        def success(outcome: BatchOutcome) -> None:
            self._set_busy(False)
            if outcome.cancelled:
                prefix = "Batch cancelled"
            elif outcome.failed_count:
                prefix = "Batch partially applied"
            else:
                prefix = "Batch finished"
            headline = (
                f"{prefix}: {outcome.success_count} moved, "
                f"{outcome.skipped_count} skipped, {outcome.failed_count} failed."
            )
            self._show_outcome(plan, outcome, headline)
            if outcome.cancelled or outcome.failed_count:
                self._banner.show(headline, color=theme.WARNING)
            self.ctx.last_plan = None
            if outcome.success_count and self.ctx.last_scan is not None:
                # Rescan so the file list reflects the new locations.
                self.app.start_scan(str(root), self.ctx.last_scan.options.recursive)

        def failure(message: str) -> None:
            self._set_busy(False)
            dialogs.error(self, "Organization failed", message)

        if self.runner.submit(APPLY_KEY, work, on_success=success, on_error=failure, on_progress=self.notify):
            self._set_busy(True)

    def _set_busy(self, busy: bool) -> None:
        self._apply_button.configure(state="disabled" if busy else "normal")

    def _show_outcome(self, plan: OrganizePlan, outcome: BatchOutcome, headline: str) -> None:
        self._render_plan(plan, outcome)
        lines = [headline]
        if outcome.batch_id:
            lines.append(f"Batch ID: {outcome.batch_id} (see History to review or undo)")
        for item in outcome.outcomes:
            if item.status in (STATUS_FAILED, STATUS_SKIPPED):
                lines.append(f"  {item.status.upper()}: {item.source} — {item.reason}")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.insert("end", "\n".join(lines) + "\n")
        self._log.configure(state="disabled")
        self.notify(headline)

    # ----- lifecycle -----------------------------------------------------

    def set_scan_running(self, running: bool) -> None:
        self._scan_bar.set_running(running)

    def refresh(self) -> None:
        scan = self.ctx.last_scan
        if scan is not None:
            self._scan_bar.set_folder(str(scan.root))
            categories = ["All categories", *sorted(scan.category_counts)]
            self._category.configure(values=categories)
            if self._category.get() not in categories:
                self._category.set("All categories")
            if scan.cancelled:
                self._banner.show("The last scan was cancelled, so these results are incomplete.")
            elif scan.issues:
                self._banner.show(f"{len(scan.issues)} item(s) could not be read and were skipped.")
            else:
                self._banner.hide()
            self._apply_filters()
        else:
            self._banner.hide()
            self._apply_filters()
        self._render_plan(self._current_plan())
