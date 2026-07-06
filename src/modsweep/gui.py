"""Cross-platform GUI over the modsweep pipeline (PySide6).

A thin front-end: it reads the same modsweep.toml, resolves sources through
the same pipeline as the CLI (announcements included), and renders the same
report. Deliberately, no custom palette or stylesheet is set anywhere, so
Qt's default style follows the operating system's light/dark theme (Qt 6.5+
tracks the OS color scheme natively on Windows and macOS).

Requires the `gui` extra: `uv sync --extra gui`, then `modsweep-gui
[config.toml]`.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

try:
    from PySide6.QtCore import QThread, Signal
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QHBoxLayout,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QVBoxLayout,
        QWidget,
    )
except ImportError:  # pragma: no cover - exercised only without the extra
    print(
        "modsweep-gui requires the gui extra: uv sync --extra gui",
        file=sys.stderr,
    )
    raise

from . import config, state, sweep as sweep_mod
from .cache import HashCache
from .cli import DEFAULT_CACHE, _quarantine_dir_for, config_sources, load_manifests
from .hashutil import hash_file
from .manifest import Manifest
from .matcher import STALE, UNCLAIMED, match
from .report import summarize
from .scanner import scan


class Worker(QThread):
    """Runs one pipeline action off the UI thread.

    stderr (source-resolution announcements) is captured and replayed into
    the console when the action finishes.
    """

    line = Signal(str)
    progress = Signal(int, int)  # done, total; total 0 hides the bar
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:  # pragma: no cover - thread body, covered via smoke
        buffer = io.StringIO()
        try:
            with redirect_stderr(buffer):
                self._fn(self)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            for announced in buffer.getvalue().splitlines():
                self.line.emit(announced)
            self.progress.emit(0, 0)


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path | None = None):
        super().__init__()
        self.setWindowTitle("modsweep")
        self.resize(1100, 700)
        self._worker: Worker | None = None

        self.sources_list = QListWidget()
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.progress = QProgressBar()
        self.progress.hide()

        self.buttons = {
            "open": QPushButton("Open Config..."),
            "refresh": QPushButton("Refresh Sources"),
            "report": QPushButton("Report"),
            "hash": QPushButton("Hash Candidates"),
            "dry": QPushButton("Sweep (Dry Run)"),
            "apply": QPushButton("Sweep + Apply..."),
        }
        self.buttons["open"].clicked.connect(self.open_config)
        self.buttons["refresh"].clicked.connect(self.refresh_sources)
        self.buttons["report"].clicked.connect(self.run_report)
        self.buttons["hash"].clicked.connect(self.run_hash_candidates)
        self.buttons["dry"].clicked.connect(lambda: self.run_sweep(apply=False))
        self.buttons["apply"].clicked.connect(lambda: self.run_sweep(apply=True))

        bar = QHBoxLayout()
        for b in self.buttons.values():
            bar.addWidget(b)
        bar.addStretch()

        splitter = QSplitter()
        splitter.addWidget(self.sources_list)
        splitter.addWidget(self.console)
        splitter.setStretchFactor(1, 3)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(bar)
        layout.addWidget(splitter)
        layout.addWidget(self.progress)
        self.setCentralWidget(root)

        self.config_path = config_path
        self.cfg = config.load(config_path)
        self._show_config_status()
        self.refresh_sources()

    # --- config / sources -------------------------------------------------

    def _show_config_status(self) -> None:
        shown = self.config_path or Path(config.DEFAULT_NAME)
        self.statusBar().showMessage(
            f"config: {shown}   downloads: {self.cfg.downloads or '<unset>'}"
        )

    def open_config(self) -> None:  # pragma: no cover - native dialog
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open modsweep config", "", "TOML (*.toml)"
        )
        if chosen:
            self.config_path = Path(chosen)
            self.cfg = config.load(self.config_path)
            self._show_config_status()
            self.refresh_sources()

    def _load_active(self, worker: Worker | None = None) -> list[Manifest]:
        manifests = load_manifests(
            config_sources(self.cfg), self.cfg.exclude, self.cfg.latest_only
        )
        state_path = self._cache_path().parent / state.STATE_NAME
        for label, source in state.vanished(state.read(state_path), manifests):
            message = (
                f"warning: previously-active source vanished: {label} "
                f"({source} no longer exists)"
            )
            (worker.line.emit if worker else self.log)(message)
        state.write(state_path, manifests)
        return manifests

    def refresh_sources(self) -> None:
        self.sources_list.clear()
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            manifests = self._load_active()
        for announced in buffer.getvalue().splitlines():
            self.log(announced)
        for m in manifests:
            self.sources_list.addItem(f"{m.label}  ({len(m.entries)} entries)")
        self.log(f"{len(manifests)} active source(s) loaded.")

    # --- actions ------------------------------------------------------------

    def run_report(self) -> None:
        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            files = scan(self.cfg.downloads)
            cache = HashCache(self._cache_path())
            try:
                results = match(files, manifests, cache)
            finally:
                cache.close()
            worker.line.emit(summarize(results, manifests))

        self._start(action)

    def run_hash_candidates(self) -> None:
        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            files = [f for f in scan(self.cfg.downloads) if not f.is_meta]
            cache = HashCache(self._cache_path())
            try:
                results = match(files, manifests, cache)
                wanted = {
                    r.disk.rel
                    for r in results
                    if r.status in (STALE, UNCLAIMED) and not r.sidecar
                }
                pending = [
                    f for f in files if f.rel in wanted and cache.get(f) is None
                ]
                worker.line.emit(f"{len(pending):,} candidate file(s) to hash.")
                for i, disk in enumerate(pending, 1):
                    xxh64_b64, crc32 = hash_file(disk.path)
                    cache.put(disk, xxh64_b64, crc32)
                    worker.progress.emit(i, len(pending))
            finally:
                cache.close()
            worker.line.emit("Hashing done - run Report to see verified results.")

        self._start(action)

    def run_sweep(self, apply: bool) -> None:
        if apply:  # pragma: no cover - native dialog
            answer = QMessageBox.question(
                self,
                "Apply sweep?",
                "Move all candidates to quarantine? (Restorable via the CLI:"
                " modsweep restore <batch>)",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            quarantine = _quarantine_dir_for(self.cfg.downloads, self.cfg.quarantine)
            files = scan(self.cfg.downloads)
            cache = HashCache(self._cache_path())
            try:
                results = match(files, manifests, cache)
                plan = sweep_mod.plan(results, cache)
            finally:
                cache.close()
            worker.line.emit(
                f"Sweep plan: {len(plan.ready):,} files, "
                f"{plan.ready_bytes / (1 << 30):,.2f} GB -> {quarantine}"
            )
            if plan.refused:
                worker.line.emit(
                    f"Refused (hash never checked): {len(plan.refused):,} "
                    f"file(s) - use Hash Candidates first"
                )
            if not apply:
                worker.line.emit("Dry run - nothing moved.")
                return
            if not plan.ready:
                worker.line.emit("Nothing to move.")
                return
            batch = sweep_mod.execute(plan, quarantine)
            worker.line.emit(f"Moved {len(plan.ready):,} files to {batch}")

        self._start(action)

    # --- plumbing -----------------------------------------------------------

    def log(self, text: str) -> None:
        self.console.appendPlainText(text)

    def _cache_path(self) -> Path:
        return self.cfg.cache or DEFAULT_CACHE

    def _start(self, fn) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # one action at a time
        self._set_busy(True)
        self._worker = Worker(fn, parent=self)
        self._worker.line.connect(self.log)
        self._worker.progress.connect(self._on_progress)
        self._worker.failed.connect(lambda msg: self.log(f"error: {msg}"))
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.hide()
            return
        self.progress.show()
        self.progress.setMaximum(total)
        self.progress.setValue(done)

    def _set_busy(self, busy: bool) -> None:
        for name, button in self.buttons.items():
            if name != "open":
                button.setEnabled(not busy)


def main() -> int:  # pragma: no cover - event loop
    app = QApplication(sys.argv)
    app.setApplicationName("modsweep")
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
