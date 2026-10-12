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
from collections.abc import Callable
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

    def __init__(self, widget: Any) -> None:
        self._widget = widget
        self._inbox: queue.Queue[Callable[[], None]] = queue.Queue()
        self._busy: dict[str, threading.Event] = {}
        self._polling = False

    def is_busy(self, key: str) -> bool:
        return key in self._busy

    def any_busy(self) -> bool:
        return bool(self._busy)

    def cancel(self, key: str) -> None:
        event = self._busy.get(key)
        if event is not None:
            event.set()

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
        if key in self._busy:
            return False
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
                self._post(lambda: self._finish(key, lambda: on_error(message)))
            else:
                self._post(lambda: self._finish(key, lambda: on_success(result)))

        threading.Thread(target=run, name=f"task-{key}", daemon=True).start()
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
        try:
            while True:
                try:
                    action = self._inbox.get_nowait()
                except queue.Empty:
                    break
                action()
        finally:
            if self._busy or not self._inbox.empty():
                self._widget.after(POLL_INTERVAL_MS, self._drain)
            else:
                self._polling = False
