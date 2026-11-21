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


def test_finished_worker_connections_are_not_retained(database) -> None:
    widget = _FakeWidget()
    runner = TaskRunner(widget, worker_cleanup=database.close_thread_connection)
    main_connection = database.connection()
    completed = []
    for index in range(8):
        assert runner.submit(
            "work",
            lambda _task: database.query("SELECT 1")[0][0],
            on_success=completed.append,
            on_error=lambda reason: (_ for _ in ()).throw(AssertionError(reason)),
        )
        deadline = time.monotonic() + 5
        while runner.workers_active() and time.monotonic() < deadline:
            time.sleep(0.01)
        runner._drain()
        assert completed == [1] * (index + 1)
        assert database._connections == {main_connection}


def test_failed_worker_bootstrap_closes_only_its_connection(database, monkeypatch) -> None:
    import sqlite3

    from smart_file_organizer.database import db as db_module

    existing = database.connection()
    connect = sqlite3.connect
    failed = threading.Event()
    closed = threading.Event()

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode = WAL":
                raise sqlite3.OperationalError("bootstrap failed")
            return super().execute(sql, *args)

        def close(self):
            super().close()
            closed.set()

    monkeypatch.setattr(db_module.sqlite3, "connect", lambda *a, **kw: connect(*a, factory=FailingConnection, **kw))

    def worker():
        try:
            database.connection()
        except sqlite3.OperationalError:
            failed.set()
        finally:
            database.close_thread_connection()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive() and failed.is_set() and closed.is_set()
    assert database._connections == {existing}
    assert existing.execute("SELECT 1").fetchone()[0] == 1
