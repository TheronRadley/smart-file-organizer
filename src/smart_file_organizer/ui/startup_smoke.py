"""Exercise the real desktop window and file workflow in a disposable directory."""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from smart_file_organizer.core.models import ScanOptions
from smart_file_organizer.core.organizer import build_plan, execute_plan
from smart_file_organizer.core.scanner import scan_folder
from smart_file_organizer.core.undo import preview_undo, undo_batch
from smart_file_organizer.ui.app_window import NAV_ITEMS

if TYPE_CHECKING:
    from smart_file_organizer.ui.app_window import App

logger = logging.getLogger(__name__)
SMOKE_MARKER = "smoke-ok.txt"


def smoke_round_trip(app: App) -> None:
    """Navigate every page, preview, apply and undo one synthetic file."""
    for key, _ in NAV_ITEMS:
        app.show_page(key)
        app.update_idletasks()
        if not app.page(key).winfo_exists():
            raise RuntimeError(f"Page {key!r} did not open")
    with TemporaryDirectory(prefix="sfo-smoke-files-") as scratch:
        root = Path(scratch)
        original = root / "example.txt"
        original.write_bytes(b"synthetic smoke test file\n")
        classifier = app.ctx.classifier
        scan = scan_folder(root, ScanOptions(), classifier)
        plan = build_plan(scan, classifier)
        app.ctx.last_scan, app.ctx.last_plan = scan, plan
        app.show_page("organize")
        app.update_idletasks()
        if len(plan.ready) != 1 or not original.is_file():
            raise RuntimeError("Preview did not include the untouched smoke-test file")
        outcome = execute_plan(plan, history=app.ctx.history)
        destination = root / "Documents" / "example.txt"
        if outcome.success_count != 1 or outcome.batch_id is None or original.exists() or not destination.is_file():
            raise RuntimeError(f"Could not apply smoke-test plan: {outcome.outcomes!r}")
        app.show_page("history")
        app.update_idletasks()
        if not preview_undo(outcome.batch_id, app.ctx.history).can_undo:
            raise RuntimeError("History did not offer Undo for the smoke-test batch")
        undo = undo_batch(outcome.batch_id, app.ctx.history)
        if not undo.fully_reversed or destination.exists() or original.read_bytes() != b"synthetic smoke test file\n":
            raise RuntimeError(f"Could not undo smoke-test batch: {undo!r}")
        app.ctx.last_scan = None
        app.ctx.last_plan = None


def schedule_smoke(app: App) -> int:
    """Run the check inside the event loop and return a process exit code."""
    exit_code = 1

    def work() -> None:
        nonlocal exit_code
        try:
            smoke_round_trip(app)
            (app.ctx.data_dir / SMOKE_MARKER).write_text("passed\n", encoding="utf-8")
            exit_code = 0
        except Exception:
            logger.exception("Desktop smoke test failed")
        finally:
            app.on_close()

    app.after(0, work)
    app.mainloop()
    return exit_code
