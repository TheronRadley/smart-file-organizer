"""Shutdown behaviour of the background task runner (audit P0).

The window must never be torn down (and the database must never be closed)
while a worker is still moving files. ``TaskRunner.shutdown`` therefore
cancels every task and only reports success once no worker thread is alive.
"""

from __future__ import annotations

import threading
import time

from smart_file_organizer.ui.components.tasks import TaskRunner


class _FakeWidget:
    """Minimal stand-in for a Tk widget: records scheduled callbacks, runs none."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[int, object]] = []

    def after(self, delay: int, callback: object) -> None:
        self.scheduled.append((delay, callback))


def test_shutdown_cancels_and_waits_for_workers() -> None:
    runner = TaskRunner(_FakeWidget())
    finished = threading.Event()

    def work(task) -> str:
        task.cancel_event.wait(30)  # runs until cancelled
        finished.set()
        return "done"

    assert runner.submit("op", work, on_success=lambda _r: None, on_error=lambda _m: None)
    assert runner.shutdown(timeout=5.0) is True
    assert finished.is_set(), "shutdown returned before the worker actually finished"


def test_shutdown_reports_false_while_a_worker_ignores_cancellation() -> None:
    runner = TaskRunner(_FakeWidget())

    def stubborn(task) -> str:
        time.sleep(0.6)  # a filesystem operation already in progress finishes first
        return "done"

    assert runner.submit("op", stubborn, on_success=lambda _r: None, on_error=lambda _m: None)
    assert runner.shutdown(timeout=0.05) is False, "a live worker must block resource release"
    assert runner.shutdown(timeout=5.0) is True
