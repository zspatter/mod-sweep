"""Cross-platform GUI over the modsweep pipeline (PySide6).

A thin front-end: it reads the same modsweep.toml, resolves sources through
the same pipeline as the CLI (announcements included), and renders the same
report data as sortable tables. Deliberately, no custom palette or
stylesheet is set anywhere, so Qt's default style follows the operating
system's light/dark theme (Qt 6.5+ tracks the OS color scheme natively on
Windows and macOS).

Requires the `gui` extra: `uv sync --extra gui`, then `modsweep-gui
[config.toml]`.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QThread, Signal
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QMainWindow,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
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
from .report import candidate_rows, claim_rows, reclaim_bytes, status_rows
from .scanner import scan

WELCOME = """\
Welcome to modsweep. Typical flow:
  1. Report            - classify the downloads directory (read-only)
  2. Hash Candidates   - verify candidates by hash (the safety gate for sweeping)
  3. Sweep (Dry Run)   - preview exactly what would be quarantined
  4. Sweep + Apply     - move candidates to a restorable quarantine batch
Nothing is ever hard-deleted from the GUI; restore and purge live in the CLI.
"""

TOOLTIPS = {
    "open": "Choose a different modsweep.toml",
    "refresh": "Reload the active sources from the config "
    "(resolution announcements appear in the Log tab)",
    "report": "Classify every file in the downloads directory against the "
    "active sources - read-only",
    "hash": "Hash-check the current deletion candidates; sweeps refuse "
    "files whose hash was never checked",
    "dry": "Preview exactly what a sweep would quarantine - nothing is moved",
    "apply": "Move all candidates to a timestamped quarantine batch "
    "(undo with: modsweep restore <batch>)",
}


def _gb(size: float) -> str:
    return f"{size / (1 << 30):,.2f} GB"


class NumericItem(QTableWidgetItem):
    """Table cell that displays formatted text but sorts by a numeric value."""

    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value

    def __lt__(self, other) -> bool:  # noqa: D105
        if isinstance(other, NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class Worker(QThread):
    """Runs one pipeline action off the UI thread.

    stderr (source-resolution announcements) is captured and replayed into
    the log when the action finishes.
    """

    line = Signal(str)
    progress = Signal(int, int)  # done, total; total 0 hides the bar
    payload = Signal(object)  # (kind, data) delivered back to the UI thread
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
        self.resize(1200, 750)
        self._worker: Worker | None = None

        self.sources_list = QListWidget()
        self.sources_list.setAlternatingRowColors(True)
        self.sources_list.setToolTip(
            "Active sources of truth: every file they name is protected"
        )

        self._build_buttons()
        self._build_tabs()
        self.progress = QProgressBar()
        self.progress.hide()

        bar = QHBoxLayout()
        for button in self.buttons.values():
            bar.addWidget(button)
        bar.addStretch()

        splitter = QSplitter()
        splitter.addWidget(self.sources_list)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 3)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(bar)
        layout.addWidget(splitter)
        layout.addWidget(self.progress)
        self.setCentralWidget(root)

        self.log(WELCOME)
        self.config_path = config_path
        self.cfg = config.load(config_path)
        self._show_config_status()
        self.refresh_sources()

    # --- construction -------------------------------------------------------

    def _build_buttons(self) -> None:
        self.buttons = {
            "open": QPushButton("Open Config..."),
            "refresh": QPushButton("Refresh Sources"),
            "report": QPushButton("Report"),
            "hash": QPushButton("Hash Candidates"),
            "dry": QPushButton("Sweep (Dry Run)"),
            "apply": QPushButton("Sweep + Apply..."),
        }
        for name, button in self.buttons.items():
            button.setToolTip(TOOLTIPS[name])
        self.buttons["open"].clicked.connect(self.open_config)
        self.buttons["refresh"].clicked.connect(self.refresh_sources)
        self.buttons["report"].clicked.connect(self.run_report)
        self.buttons["hash"].clicked.connect(self.run_hash_candidates)
        self.buttons["dry"].clicked.connect(lambda: self.run_sweep(apply=False))
        self.buttons["apply"].clicked.connect(lambda: self.run_sweep(apply=True))

    def _build_tabs(self) -> None:
        self.summary_label = QLabel("Run Report to classify the downloads directory.")
        self.status_table = self._make_table(["Status", "Files", "Size"], sortable=False)
        self.claims_table = self._make_table(
            ["Source", "Claimed", "Unique", "Unique size"]
        )
        self.claims_table.setToolTip(
            "Unique = claimed by no other source: what retiring that source would free"
        )
        self.candidates_table = self._make_table(["Size", "Status", "Path"])

        report_tab = QWidget()
        report_layout = QVBoxLayout(report_tab)
        report_layout.addWidget(self.summary_label)
        report_layout.addWidget(self.status_table, 2)
        report_layout.addWidget(QLabel("Disk archives claimed per source:"))
        report_layout.addWidget(self.claims_table, 4)
        report_layout.addWidget(QLabel("Deletion candidates:"))
        report_layout.addWidget(self.candidates_table, 4)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        log_font = QFont()
        log_font.setFamilies(
            ["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "monospace"]
        )
        self.console.setFont(log_font)

        self.tabs = QTabWidget()
        self.tabs.addTab(report_tab, "Report")
        self.tabs.addTab(self.console, "Log")

    @staticmethod
    def _make_table(headers: list[str], sortable: bool = True) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSortingEnabled(sortable)
        table.verticalHeader().hide()
        return table

    # --- config / sources -----------------------------------------------------

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

    def _load_active(self, worker: Worker) -> list[Manifest]:
        """Resolve sources (parsing manifests is the slow part - workers only)."""
        manifests = load_manifests(
            config_sources(self.cfg), self.cfg.exclude, self.cfg.latest_only
        )
        state_path = self._cache_path().parent / state.STATE_NAME
        for label, source in state.vanished(state.read(state_path), manifests):
            worker.line.emit(
                f"warning: previously-active source vanished: {label} "
                f"({source} no longer exists)"
            )
        state.write(state_path, manifests)
        return manifests

    def refresh_sources(self) -> None:
        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            worker.payload.emit(("sources", manifests))

        self._start(action)

    # --- actions ---------------------------------------------------------------

    def run_report(self) -> None:
        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            files = scan(self.cfg.downloads)
            cache = HashCache(self._cache_path())
            try:
                results = match(files, manifests, cache)
            finally:
                cache.close()
            worker.payload.emit(("report", (manifests, results)))

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
            worker.line.emit("Hashing done.")

        self._start(action, then_report=True)

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
                f"{_gb(plan.ready_bytes)} -> {quarantine}"
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

        self._start(action, then_report=apply)

    # --- rendering ---------------------------------------------------------------

    def _on_payload(self, message: object) -> None:
        kind, data = message
        if kind == "sources":
            self._show_sources(data)
        elif kind == "report":
            manifests, results = data
            self._show_report(manifests, results)

    def _show_sources(self, manifests: list[Manifest]) -> None:
        self.sources_list.clear()
        for m in manifests:
            self.sources_list.addItem(f"{m.label}  ({len(m.entries)} entries)")
        self.log(f"{len(manifests)} active source(s) loaded.")

    def _show_report(self, manifests: list[Manifest], results) -> None:
        total_files = len(results)
        total_bytes = sum(r.disk.size for r in results)
        self.summary_label.setText(
            f"{total_files:,} files / {_gb(total_bytes)} across "
            f"{len(manifests)} sources   |   potential reclaim: "
            f"{_gb(reclaim_bytes(results))}"
        )
        self._fill(
            self.status_table,
            [
                [QTableWidgetItem(label), NumericItem(count, f"{count:,}"),
                 NumericItem(size, _gb(size))]
                for label, count, size in status_rows(results)
            ],
        )
        self._fill(
            self.claims_table,
            [
                [QTableWidgetItem(source), NumericItem(claimed, f"{claimed:,}"),
                 NumericItem(unique, f"{unique:,}"),
                 NumericItem(unique_size, _gb(unique_size))]
                for source, claimed, unique, unique_size in claim_rows(results)
            ],
        )
        self._fill(
            self.candidates_table,
            [
                [NumericItem(size, _gb(size)), QTableWidgetItem(status),
                 QTableWidgetItem(rel)]
                for size, status, rel in candidate_rows(results)
            ],
        )
        self.tabs.setCurrentIndex(0)

    @staticmethod
    def _fill(table: QTableWidget, rows: list[list[QTableWidgetItem]]) -> None:
        sortable = table.isSortingEnabled()
        table.setSortingEnabled(False)  # sorting during insert scrambles rows
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for col_index, item in enumerate(row):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row_index, col_index, item)
        table.setSortingEnabled(sortable)
        table.resizeColumnsToContents()

    # --- plumbing -----------------------------------------------------------------

    def log(self, text: str) -> None:
        self.console.appendPlainText(text)

    def _cache_path(self) -> Path:
        return self.cfg.cache or DEFAULT_CACHE

    def _start(self, fn, then_report: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # one action at a time
        self._set_busy(True)
        self._worker = Worker(fn, parent=self)
        self._worker.line.connect(self.log)
        self._worker.progress.connect(self._on_progress)
        self._worker.payload.connect(self._on_payload)
        self._worker.failed.connect(lambda msg: self.log(f"error: {msg}"))
        self._worker.finished.connect(lambda: self._on_finished(then_report))
        self._worker.start()

    def _on_finished(self, then_report: bool) -> None:
        self._set_busy(False)
        if then_report:
            self.run_report()

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
