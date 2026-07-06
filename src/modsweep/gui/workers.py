"""Thread and logging plumbing between the pipeline and the UI."""

from __future__ import annotations

import io
import logging
from contextlib import redirect_stderr

from PySide6.QtCore import QObject, QThread, Signal


class LogBridge(QObject):
    """Marshals text from worker threads onto the UI thread via a signal."""

    message = Signal(str)


class GuiLogHandler(logging.Handler):
    """Streams log records into the Log tab live via the bridge signal
    (queued across threads by Qt)."""

    def __init__(self, bridge: LogBridge):
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        self._bridge.message.emit(self.format(record))


class _LineStream(io.TextIOBase):
    """File-like object forwarding complete lines to a callback as written -
    announcements printed to stderr stream into the Log in real time."""

    def __init__(self, callback):
        super().__init__()
        self._callback = callback
        self._pending = ""

    def write(self, text: str) -> int:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._callback(line)
        return len(text)

    def flush_pending(self) -> None:
        if self._pending.strip():
            self._callback(self._pending)
        self._pending = ""


class Worker(QThread):
    """Runs one pipeline action off the UI thread.

    stderr (source-resolution announcements) is captured and replayed into
    the log when the action finishes.
    """

    line = Signal(str)
    status = Signal(str)  # one-line action summary for the status bar
    summary = Signal(str)  # action result: status bar + log + popup
    progress = Signal(int, int)  # done, total; total 0 hides the bar
    payload = Signal(object)  # (kind, data) delivered back to the UI thread
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # pragma: no cover - thread body, covered via smoke
        stream = _LineStream(self.line.emit)
        try:
            with redirect_stderr(stream):
                self._fn(self)
        except (Exception, SystemExit) as exc:
            # SystemExit must not escape a QThread: unhandled it takes down
            # the whole process, not just this action.
            self.failed.emit(str(exc))
        finally:
            stream.flush_pending()
