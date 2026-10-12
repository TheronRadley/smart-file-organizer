"""Automation: optional, explicit folder monitoring with pause, resume and stop."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.watcher import (
    WATCHER_PAUSED,
    WATCHER_RUNNING,
    FolderWatcher,
    WatchConfig,
)
from smart_file_organizer.ui import theme
from smart_file_organizer.ui.components import dialogs
from smart_file_organizer.ui.components.dialogs import choose_folder
from smart_file_organizer.ui.components.tasks import TaskContext
from smart_file_organizer.ui.components.widgets import Card, Column, FileTable
from smart_file_organizer.ui.pages.base import BasePage, button, muted_label
from smart_file_organizer.utils.formatting import format_size

APPLY_PENDING_KEY = "watch-apply"
REFRESH_MS = 1000
STABLE_SECONDS = 10.0


class AutomationPage(BasePage):
    title = "Automation"
    description = (
        "Optional folder monitoring. Nothing is watched until you add a folder and press Start. "
        "By default, new files are only listed for your approval; automatic organization must be enabled explicitly."
    )

    def build(self, body: ctk.CTkFrame) -> None:
        body.grid_columnconfigure(0, weight=1)
        how = Card(body)
        how.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(how, text="How it works", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 4)
        )
        muted_label(
            how,
            "• The folder is re-checked every few seconds (no background service, no OS hooks).\n"
            "• A file is only handled after its size and timestamp stop changing for 10 seconds, so partial copies "
            "are left alone.\n"
            "• Organized folders and excluded names are never processed again. Each file is handled once unless "
            "it changes.\n"
            "• To disable monitoring: press Stop, or remove the folder. Closing the application also stops monitoring.",
            wraplength=900,
        ).pack(anchor="w", padx=16, pady=(0, 12))

        folders = Card(body)
        folders.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        folders.grid_columnconfigure(0, weight=1)
        toolbar = ctk.CTkFrame(folders, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        button(toolbar, "Add folder…", self._add_folder, primary=True, width=140).pack(side="left")
        button(toolbar, "Remove selected", self._remove_folder, width=150).pack(side="left", padx=8)
        self._recursive = ctk.CTkCheckBox(
            toolbar, text="Include subfolders", text_color=theme.TEXT, command=self._save_options
        )
        self._recursive.pack(side="left", padx=(16, 8))
        self._auto = ctk.CTkCheckBox(
            toolbar, text="Organize automatically (no preview)", text_color=theme.TEXT, command=self._toggle_auto
        )
        self._auto.pack(side="left", padx=8)
        self._folders = FileTable(
            folders,
            [
                Column("folder", "Monitored folder", 380),
                Column("status", "Status", 110),
                Column("pending", "Waiting", 80, "e"),
                Column("last", "Last message", 360),
            ],
            height=4,
            selectmode="browse",
        )
        self._folders.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))

        controls = ctk.CTkFrame(body, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        button(controls, "Start monitoring", self._start, primary=True, width=160).pack(side="left")
        button(controls, "Pause", self._pause, width=110).pack(side="left", padx=8)
        button(controls, "Resume", self._resume, width=110).pack(side="left")
        button(controls, "Stop", self._stop, width=110).pack(side="left", padx=8)
        button(controls, "Apply waiting files…", self._apply_pending, width=190).pack(side="left", padx=(16, 0))

        pending = Card(body)
        pending.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        ctk.CTkLabel(
            pending, text="Waiting for approval (preview mode)", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self._pending = FileTable(
            pending,
            [
                Column("source", "File", 360),
                Column("destination", "Would move to", 420),
                Column("size", "Size", 90, "e"),
            ],
            height=5,
            selectmode="browse",
        )
        self._pending.pack(fill="x", padx=12, pady=(0, 12))

        log_card = Card(body)
        log_card.grid(row=4, column=0, sticky="ew")
        ctk.CTkLabel(
            log_card, text="Activity log (this session)", text_color=theme.TEXT, font=ctk.CTkFont(weight="bold")
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self._log = ctk.CTkTextbox(log_card, height=150, fg_color=theme.SURFACE_ALT, text_color=theme.TEXT)
        self._log.pack(fill="x", padx=12, pady=(0, 12))
        self._log.configure(state="disabled")
        self._timer: str | None = None

    # ----- lifecycle -----------------------------------------------------

    def on_show(self) -> None:
        _set_checkbox(self._recursive, self.ctx.settings.watch_recursive)
        _set_checkbox(self._auto, self.ctx.settings.watch_auto_apply)
        self.refresh()
        self._tick()

    def on_hide(self) -> None:
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None

    def _tick(self) -> None:
        self._timer = None
        self.refresh()
        self._timer = self.after(REFRESH_MS, self._tick)

    def refresh(self) -> None:
        self._folders.clear()
        for folder in self.ctx.settings.watch_folders:
            watcher = self.ctx.watches.get(Path(folder))
            status = _describe_status(watcher.status if watcher else "stopped")
            pending = str(len(watcher.pending)) if watcher else "0"
            last = watcher.events[-1].text.split("  ", 1)[-1] if watcher and watcher.events else "—"
            if watcher and watcher.last_error:
                last = watcher.last_error
            self._folders.insert([folder, status, pending, last], folder)

        self._pending.clear()
        for watcher in self.ctx.watches.watchers():
            for item in watcher.pending:
                self._pending.insert([item.source.name, str(item.destination or "—"), format_size(item.size)], item)

        lines: list[str] = []
        for watcher in self.ctx.watches.watchers():
            lines.extend(f"[{watcher.folder.name}] {event.text}" for event in watcher.events)
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.insert("end", "\n".join(sorted(lines)) + ("\n" if lines else "No monitoring activity yet.\n"))
        self._log.configure(state="disabled")

    # ----- folder list ---------------------------------------------------

    def _add_folder(self) -> None:
        chosen = choose_folder(self, "Choose a folder to monitor")
        if not chosen:
            return
        path = Path(chosen)
        if not path.is_dir():
            dialogs.error(self, "Not a folder", "Choose an existing folder.")
            return
        if path.is_symlink():
            dialogs.error(self, "Symbolic link", "Choose the real folder rather than a link to it.")
            return
        if str(path) in self.ctx.settings.watch_folders:
            dialogs.info(self, "Already monitored", "That folder is already in the list.")
            return
        self.ctx.settings.watch_folders.append(str(path))
        self.ctx.save_settings()
        self.refresh()

    def _remove_folder(self) -> None:
        folder = self._folders.selected_payload()
        if folder is None:
            dialogs.info(self, "Select a folder", "Select a folder in the list first.")
            return
        self.ctx.watches.remove(Path(folder))
        self.ctx.settings.watch_folders = [f for f in self.ctx.settings.watch_folders if f != folder]
        self.ctx.save_settings()
        self.refresh()

    # ----- options -------------------------------------------------------

    def _save_options(self) -> None:
        self.ctx.settings.watch_recursive = bool(self._recursive.get())
        self.ctx.save_settings()

    def _toggle_auto(self) -> None:
        if self._auto.get():
            confirmed = dialogs.confirm(
                self,
                "Enable automatic organization",
                "Automatic mode moves stable files into organized folders without asking you first.\n\n"
                "Collision rules, exclusions and the disabled-category list still apply, and every move is recorded "
                "so it can be undone. Enable automatic organization?",
            )
            if not confirmed:
                self._auto.deselect()
                return
        self.ctx.settings.watch_auto_apply = bool(self._auto.get())
        self.ctx.save_settings()

    # ----- controls ------------------------------------------------------

    def _start(self) -> None:
        folders = self.ctx.settings.watch_folders
        if not folders:
            dialogs.info(self, "No folders", "Add at least one folder to monitor first.")
            return
        settings = self.ctx.settings
        config = WatchConfig(
            organize_by=settings.organize_by,
            collision_strategy=settings.collision_strategy,
            recursive=bool(self._recursive.get()),
            auto_apply=bool(self._auto.get()),
            scan_options=ScanOptions(
                recursive=bool(self._recursive.get()),
                excluded_folders=tuple(settings.excluded_folders),
                excluded_patterns=tuple(settings.excluded_patterns),
            ),
            disabled_categories=frozenset(settings.disabled_categories),
            interval_seconds=float(settings.watch_interval_seconds),
            stable_seconds=STABLE_SECONDS,
        )
        started = 0
        for folder in folders:
            path = Path(folder)
            if self.ctx.watches.get(path) is not None:
                continue
            watcher = FolderWatcher(path, config, classifier=self.ctx.classifier, history=self.ctx.history)
            self.ctx.watches.add(watcher)
            watcher.start()
            started += 1
        self.notify(
            f"Monitoring {len(self.ctx.watches)} folder(s)." if started else "Already monitoring the listed folders."
        )
        self.refresh()

    def _pause(self) -> None:
        for watcher in self.ctx.watches.watchers():
            watcher.pause()
        self.refresh()

    def _resume(self) -> None:
        for watcher in self.ctx.watches.watchers():
            watcher.resume()
        self.refresh()

    def _stop(self) -> None:
        for watcher in self.ctx.watches.watchers():
            watcher.stop()
        self.notify("Monitoring stopped.")
        self.refresh()

    def _apply_pending(self) -> None:
        watchers = [w for w in self.ctx.watches.watchers() if w.pending]
        if not watchers:
            dialogs.info(self, "Nothing waiting", "No files are waiting for approval.")
            return
        total = sum(len(w.pending) for w in watchers)
        if not dialogs.confirm(
            self, "Apply waiting files", f"Move {total} waiting file(s) into their organized folders?"
        ):
            return

        def work(task: TaskContext) -> tuple[int, int, int]:
            moved = skipped = failed = 0
            for watcher in watchers:
                outcome = watcher.apply_pending()
                if outcome is not None:
                    moved += outcome.success_count
                    skipped += outcome.skipped_count
                    failed += outcome.failed_count
            return moved, skipped, failed

        def success(counts: tuple[int, int, int]) -> None:
            moved, skipped, failed = counts
            if failed or skipped:
                self.notify(f"Waiting files processed: {moved} moved, {skipped} skipped, {failed} failed.")
            else:
                self.notify(f"Moved {moved} waiting file(s).")
            self.refresh()

        self.runner.submit(
            APPLY_PENDING_KEY, work, on_success=success, on_error=lambda m: dialogs.error(self, "Could not apply", m)
        )


def _set_checkbox(checkbox: ctk.CTkCheckBox, checked: bool) -> None:
    if checked:
        checkbox.select()
    else:
        checkbox.deselect()


def _describe_status(status: str) -> str:
    return {
        WATCHER_RUNNING: "Running",
        WATCHER_PAUSED: "Paused",
        "stopped": "Stopped",
    }.get(status, status.title())
