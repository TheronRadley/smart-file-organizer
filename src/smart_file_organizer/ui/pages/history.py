"""History: recorded batches, their operations, and guarded undo."""

from __future__ import annotations

import customtkinter as ctk

from smart_file_organizer.core.export import ExportError, history_export, write_export
from smart_file_organizer.core.models import (
    OP_CREATE_FOLDER,
    BatchRecord,
    OperationRecord,
)
from smart_file_organizer.core.recovery import UNFINISHED_BATCH_STATUSES, RecoveryError, recovery_steps
from smart_file_organizer.core.undo import (
    UndoError,
    UndoPreview,
    preview_undo,
    undo_batch,
)
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.tasks import TaskContext
from smart_file_organizer.ui.components.widgets import Banner, Card, Column, FileTable
from smart_file_organizer.ui.pages.base import BasePage, button, muted_label
from smart_file_organizer.utils.formatting import format_size, format_timestamp

UNDO_KEY = "undo"


class HistoryPage(BasePage):
    title = "History"
    description = (
        "Every organization batch is recorded with its original and new location. Undo reverses a batch only when "
        "every file is still where it was left; otherwise nothing is changed and the reasons are shown."
    )

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_rowconfigure(1, weight=1)
        body.grid_rowconfigure(3, weight=1)
        toolbar = ctk.CTkFrame(body, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        button(toolbar, "Undo selected batch…", self._undo, primary=True, width=200).pack(side="left")
        button(toolbar, "Refresh", self.refresh, width=110).pack(side="left", padx=8)
        button(toolbar, "Clear history…", self._clear, width=150).pack(side="left")
        button(toolbar, "Recovery guidance…", self._recovery, width=170).pack(side="left", padx=8)
        button(toolbar, "Export history…", self._export, width=150).pack(side="left")
        self._note = muted_label(toolbar, "")
        self._note.pack(side="right")

        self._banner = Banner(body)
        self._banner.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        batches = Card(body)
        batches.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        batches.grid_columnconfigure(0, weight=1)
        batches.grid_rowconfigure(0, weight=1)
        self._batches = FileTable(
            batches,
            [
                Column("when", "Started", 140),
                Column("kind", "Kind", 90),
                Column("folder", "Folder", 200),
                Column("status", "Status", 120),
                Column("ops", "Operations", 90, "e"),
                Column("note", "Summary", 360),
            ],
            on_select=self._load_operations,
            height=7,
            selectmode="browse",
        )
        self._batches.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        details = Card(body)
        details.grid(row=3, column=0, sticky="nsew")
        details.grid_columnconfigure(0, weight=1)
        details.grid_rowconfigure(0, weight=1)
        self._operations = FileTable(
            details,
            [
                Column("when", "Time", 130),
                Column("op", "Operation", 90),
                Column("source", "Original location", 340),
                Column("destination", "Current / new location", 340),
                Column("size", "Size", 80, "e"),
                Column("status", "Status", 100),
                Column("undo", "Undo", 130),
                Column("error", "Error / reason", 260),
            ],
            height=8,
            selectmode="browse",
        )
        self._operations.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self._operations.set_tag_style("bad", theme.DANGER)

    def refresh(self) -> None:
        self._batches.clear()
        self._operations.clear()
        for batch in self.ctx.history.list_batches():
            self._batches.insert(
                [
                    format_timestamp(batch.created_at),
                    "Preview" if batch.dry_run else batch.kind.title(),
                    batch.root_name,
                    batch.status.replace("_", " ").title(),
                    str(batch.operation_count),
                    batch.note or "",
                ],
                batch,
            )
        count = len(self.ctx.history.list_batches())
        self._note.configure(text=f"{count:,} batch(es) recorded")

    def _selected_batch(self) -> BatchRecord | None:
        batch = self._batches.selected_payload()
        if batch is None:
            dialogs.info(self, "Select a batch", "Select a batch in the list first.")
        return batch

    def _load_operations(self) -> None:
        batch = self._batches.selected_payload()
        self._operations.clear()
        self._banner.hide()
        if batch is None:
            return
        for operation in self.ctx.history.list_operations(batch.id):
            self._operations.insert(
                _operation_row(operation),
                operation,
                tags=("bad",) if operation.status in ("failed", "interrupted") else (),
            )
        if batch.status in UNFINISHED_BATCH_STATUSES:
            # An unfinished batch is exactly when the user needs to know what to do next.
            self._recovery(silent_when_clean=True)

    def _undo(self) -> None:
        batch = self._selected_batch()
        if batch is None:
            return
        try:
            preview = preview_undo(batch.id, self.ctx.history)
        except UndoError as exc:
            dialogs.error(self, "Cannot undo", str(exc))
            return
        if not preview.can_undo:
            self._show_blocked(preview)
            return
        extra = (
            " Empty folders created by this batch will be removed."
            if any(op.op_type == OP_CREATE_FOLDER for op in self.ctx.history.list_operations(batch.id))
            else ""
        )
        if not dialogs.confirm(
            self,
            "Undo batch",
            f"Move {len(preview.items)} file(s) back to their original locations?{extra}\n\n"
            "This is only done if every file is still exactly where it was organized.",
        ):
            return

        def work(task: TaskContext):
            return undo_batch(batch.id, self.ctx.history)

        def success(result) -> None:
            self.notify(f"Undo finished: {result.restored} restored, {result.failed} failed, {result.skipped} skipped.")
            if result.messages:
                self._banner.show("\n".join(result.messages[:6]), color=theme.WARNING)
            else:
                self._banner.hide()
            self.refresh()

        self.runner.submit(UNDO_KEY, work, on_success=success, on_error=lambda m: dialogs.error(self, "Undo failed", m))

    def _show_blocked(self, preview: UndoPreview) -> None:
        reasons = [f"• {item.current.name}: {item.reason}" for item in preview.blocked[:10]]
        more = len(preview.blocked) - len(reasons)
        if more > 0:
            reasons.append(f"…and {more} more.")
        message = "\n".join(reasons) or "Nothing is left to undo."
        self._banner.show(
            f"Undo refused: {len(preview.blocked)} file(s) cannot be restored safely. "
            f"No files were changed.\n{message}",
            color=theme.DANGER,
        )

    def _recovery(self, *, silent_when_clean: bool = False) -> None:
        batch = self._selected_batch() if not silent_when_clean else self._batches.selected_payload()
        if batch is None:
            return
        try:
            steps = recovery_steps(batch.id, self.ctx.history)
        except RecoveryError as exc:
            dialogs.error(self, "No guidance", str(exc))
            return
        if not steps:
            if not silent_when_clean:
                self._banner.show(
                    "Every move in this batch finished cleanly. Nothing needs attention.", color=theme.SUCCESS
                )
            return
        lines = "\n".join(f"• {step.text}" for step in steps[:8])
        extra = len(steps) - 8
        if extra > 0:
            lines += f"\n…and {extra} more."
        self._banner.show(f"{len(steps)} move(s) need a decision:\n{lines}", color=theme.WARNING)

    def _export(self) -> None:
        if not self.ctx.history.list_batches(limit=1):
            dialogs.info(self, "Nothing to export", "History is empty.")
            return
        path = dialogs.choose_save_path(self, "Export history", "history.csv")
        if not path:
            return
        try:
            # The native save dialog has already confirmed replacing an existing file.
            written = write_export(history_export(self.ctx.history), path, overwrite=True)
        except ExportError as exc:
            dialogs.error(self, "Could not export", str(exc))
            return
        self.notify(f"History exported to {written}")

    def _clear(self) -> None:
        if not dialogs.confirm(
            self,
            "Clear history",
            "This deletes the history records only. Files on disk are not touched, but you will no longer be able "
            "to undo past batches.",
        ):
            return
        self.ctx.history.clear()
        self.refresh()
        self.notify("History cleared. Your files were not changed.")


def _operation_row(operation: OperationRecord) -> list[str]:
    undo = operation.undo_status or ("—" if operation.op_type == OP_CREATE_FOLDER else "not undone")
    return [
        format_timestamp(operation.created_at),
        operation.op_type.replace("_", " "),
        operation.source_path or "—",
        operation.destination_path or "—",
        format_size(operation.size_bytes) if operation.size_bytes is not None else "—",
        operation.status.title(),
        undo.replace("_", " ").title(),
        operation.error or operation.undo_error or "",
    ]
