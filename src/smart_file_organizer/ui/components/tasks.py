"""Background execution that keeps the interface responsive.

Work runs on a daemon thread. Results, errors and progress are queued and
delivered on the Tk main thread by polling with ``after``; widgets are never
touched from worker threads.
"""

from __future__ import annotations

import logging
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from functools import partial
from typing import Any, TypeVar

from smart_file_organizer.core.scanner import ScanError
from smart_file_organizer.core.undo import UndoError
from smart_file_organizer.utils.paths import UnsafePathError

logger = logging.getLogger(__name__)
T = TypeVar("T")

POLL_INTERVAL_MS = 40
GENERIC_ERROR = "Something unexpected went wrong. The details were written to the application log."


class TaskContext:
    """Handed to background work: cancellation flag and progress reporting."""

    def __init__(self, cancel_event: threading.Event, report: Callable[[str], None]) -> None:
        self._cancel_event = cancel_event
        self._report = report

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def report(self, message: str) -> None:
        self._report(message)


def user_message(exc: BaseException) -> str:
    """Friendly message for an exception. Expected errors are shown; others are generic."""
    if isinstance(exc, (ScanError, UnsafePathError, UndoError, ValueError)):
        return str(exc)
    if isinstance(exc, OSError):
        return f"A filesystem error occurred: {exc.strerror or exc.__class__.__name__}."
    if isinstance(exc, sqlite3.Error):
        return "The history database could not be updated. Check the log for details."
    return GENERIC_ERROR


class TaskRunner:
    """Runs one background task per key and marshals callbacks back to the UI thread."""

    def __init__(self, widget: Any, *, worker_cleanup: Callable[[], None] | None = None) -> None:
        self._widget = widget
        self._inbox: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy: dict[str, threading.Event] = {}
        self._threads: set[threading.Thread] = set()
        self._polling = False
        self._closing = False
        self._worker_cleanup = worker_cleanup

    def is_busy(self, key: str) -> bool:
        return key in self._busy

    def any_busy(self) -> bool:
        return bool(self._busy) or self.workers_active()

    def cancel(self, key: str) -> None:
        event = self._busy.get(key)
        if event is not None:
            event.set()

    def request_shutdown(self) -> None:
        """Cancel all tasks without blocking Tk and reject new work."""
        self._closing = True
        for event in self._busy.values():
            event.set()

    def workers_active(self) -> bool:
        self._threads = {thread for thread in self._threads if thread.is_alive()}
        return bool(self._threads)

    def shutdown(self, timeout: float = 10.0) -> bool:
        """Signal all tasks, then wait up to a shared deadline. Not for Tk callbacks."""
        self.request_shutdown()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        return not self.workers_active()

    def submit(
        self,
        key: str,
        work: Callable[[TaskContext], T],
        *,
        on_success: Callable[[T], None],
        on_error: Callable[[str], None],
        on_progress: Callable[[str], None] | None = None,
    ) -> bool:
        """Start ``work`` in the background. Returns False if a task with ``key`` is already running."""
        if self._closing or key in self._busy:
            return False
        self.workers_active()  # Reap exited threads on the UI thread only.
        cancel_event = threading.Event()
        self._busy[key] = cancel_event
        progress = (lambda message: self._post(lambda: on_progress(message))) if on_progress else (lambda _m: None)
        context = TaskContext(cancel_event, progress)

        def run() -> None:
            try:
                result = work(context)
            except Exception as exc:  # Logged with traceback; the user sees only a friendly message.
                logger.exception("Background task %s failed", key)
                message = user_message(exc)
                action = partial(on_error, message)
            else:
                action = partial(on_success, result)
            finally:
                if self._worker_cleanup is not None:
                    try:
                        self._worker_cleanup()
                    except Exception:
                        logger.exception("Could not release background resources for %s", key)
            self._post(lambda: self._finish(key, action))

        thread = threading.Thread(target=run, name=f"task-{key}", daemon=True)
        self._threads.add(thread)
        thread.start()
        self._ensure_polling()
        return True

    def _finish(self, key: str, callback: Callable[[], None]) -> None:
        self._busy.pop(key, None)
        callback()

    def _post(self, action: Callable[[], None]) -> None:
        self._inbox.put(action)

    def _ensure_polling(self) -> None:
        if not self._polling:
            self._polling = True
            self._widget.after(POLL_INTERVAL_MS, self._drain)

    def _drain(self) -> None:
        if self._closing:
            self._polling = False
            return
        try:
            while not self._closing:
                try:
                    action = self._inbox.get_nowait()
                except queue.Empty:
                    break
                action()
        finally:
            if not self._closing and (self._busy or not self._inbox.empty()):
                self._widget.after(POLL_INTERVAL_MS, self._drain)
            else:
                self._polling = False
