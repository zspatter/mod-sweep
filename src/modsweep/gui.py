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
import logging
import subprocess
import sys
import threading
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path

try:
    from PySide6.QtCore import QSettings, Qt, QThread, Signal
    from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QProgressBar,
        QPushButton,
        QSpinBox,
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

from dataclasses import replace

from . import config, snapshot as snapshot_mod, state, sweep as sweep_mod
from .cache import HashCache
from .cli import (
    DEFAULT_CACHE,
    SourceInfo,
    _quarantine_dir_for,
    config_sources,
    exact_exclude_pattern,
    is_exact_exclude,
    load_manifests,
    survey_sources,
)
from .hashutil import hash_file
from .manifest import Manifest
from .matcher import STALE, UNCLAIMED, match
from .report import candidate_rows, claim_rows, reclaim_bytes, status_rows
from .scanner import scan

WELCOME = """\
Typical flow:

1. Report - classify the downloads directory (read-only)
2. Hash Candidates - verify candidates by hash (the safety gate for sweeping)
3. Sweep (Dry Run) - preview exactly what would be quarantined
4. Sweep + Apply - move candidates to a restorable quarantine batch

Sweeps never hard-delete: batches sit in quarantine until you Restore or
Purge them. Purge is the only permanent deletion. Hover any button for
details."""

TOOLTIPS = {
    "open": "Choose a different modsweep.toml",
    "edit": "Edit the config in a dialog: downloads folder, sources of "
    "truth, exclusions, and quarantine settings",
    "refresh": "Reload the active sources from the config "
    "(resolution announcements appear in the Log tab)",
    "report": "Classify every file in the downloads directory against the "
    "active sources - read-only",
    "hash": "Hash-check the current deletion candidates; sweeps refuse "
    "files whose hash was never checked",
    "dry": "Preview exactly what a sweep would quarantine - nothing is moved",
    "apply": "Move all candidates to a timestamped quarantine batch "
    "(undo with Restore)",
    "restore": "Move a quarantined batch back into the downloads directory",
    "purge": "PERMANENTLY delete a quarantine batch of YOUR choosing, any "
    "age - the keep_days trust period only guides the CLI's age-based "
    "purge, not this button",
    "snapshot": "Export each active source as a compact whitelist that "
    "survives deletion of the original manifest - cheap insurance before "
    "uninstalling Wabbajack",
}


RESOLUTION_HELP = """\
How sources become active (precedence: exclude > pin > latest-only > active):

- Everything found is active by default. Forgetting a list keeps its files \
- only explicit action exposes files for sweeping.
- Folder entries are walked and load implicitly; files you name yourself \
are PINNED: the latest-only filter never drops them.
- Excludes retire a list without touching any of its files.
- latest_only keeps only the newest version of each list (by list name); \
pinned files still count as versions, so pinning the newest does not \
resurrect older ones.

Retiring a list:
1. Untick it under Active sources (writes an exclude for you), add an \
exclude glob in the editor, or remove its manifest file.
2. Run Report - its uniquely-claimed archives become candidates.
3. Sweep when ready. Keep the .wabbajack (or a snapshot) so reinstating \
later is painless."""

STATUS_HELP = {
    "keep-verified": "Hash matches an active source - protected",
    "keep": "Name and size match an active source (or a name-only source "
    "such as [NoDelete] claims it) - protected",
    "stale-version": "An active source knows this file name, but not this "
    "exact file's hash - a superseded or re-uploaded version",
    "unclaimed": "No active source references this file at all",
    "meta-orphan": "A .meta sidecar whose archive is gone",
}


def _gb(size: float) -> str:
    return f"{size / (1 << 30):,.2f} GB"


class GuiLogHandler(logging.Handler):
    """Collects log records thread-safely; the UI drains them between actions."""

    def __init__(self):
        super().__init__()
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        self._records: list[str] = []
        self._records_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        with self._records_lock:
            self._records.append(self.format(record))

    def drain(self) -> list[str]:
        with self._records_lock:
            drained = self._records[:]
            self._records.clear()
        return drained


def _app_icon() -> QIcon:
    """A broom, rendered from the emoji font - good enough until a real .ico."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    font = QFont()
    font.setPointSize(40)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "\U0001f9f9")
    painter.end()
    return QIcon(pixmap)


class NumericItem(QTableWidgetItem):
    """Table cell that displays formatted text but sorts by a numeric value."""

    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value

    def __lt__(self, other) -> bool:  # noqa: D105
        if isinstance(other, NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class PathListEditor(QWidget):
    """A list of paths (or plain strings) with add/remove buttons."""

    def __init__(self, values: list, file_filter: str | None, allow_dirs: bool,
                 text_only: bool = False, parent=None):
        super().__init__(parent)
        self._file_filter = file_filter
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        for value in values:
            self.list.addItem(str(value))

        buttons = QHBoxLayout()
        if text_only:
            self.pattern_edit = QLineEdit()
            self.pattern_edit.setPlaceholderText("glob, e.g. LoreRim 2.2*")
            add = QPushButton("Add")
            add.clicked.connect(self._add_text)
            buttons.addWidget(self.pattern_edit, 1)
            buttons.addWidget(add)
        else:
            if file_filter is not None:
                add_file = QPushButton("Add File...")
                add_file.clicked.connect(self._add_file)
                buttons.addWidget(add_file)
            if allow_dirs:
                add_dir = QPushButton("Add Folder...")
                add_dir.clicked.connect(self._add_dir)
                buttons.addWidget(add_dir)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        buttons.addWidget(remove)
        buttons.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def values(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _add_text(self) -> None:
        text = self.pattern_edit.text().strip()
        if text:
            self.list.addItem(text)
            self.pattern_edit.clear()

    def _add_file(self) -> None:  # pragma: no cover - native dialog
        chosen, _ = QFileDialog.getOpenFileName(self, "Add file", "", self._file_filter)
        if chosen:
            self.list.addItem(chosen)

    def _add_dir(self) -> None:  # pragma: no cover - native dialog
        chosen = QFileDialog.getExistingDirectory(self, "Add folder")
        if chosen:
            self.list.addItem(chosen)

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))


class ConfigEditorDialog(QDialog):
    """Edit a Config with pickers; result_config() builds the outcome."""

    SOURCE_TABS = (
        ("wabbajack", "Wabbajack", "Wabbajack lists (*.wabbajack *.json)", True,
         "Wabbajack modlists - the complete source of truth (name + size + "
         "hash per archive). Add a <i>folder</i> and it is searched "
         "<b>recursively through every subdirectory</b> for .wabbajack "
         "files - pointing at the Wabbajack software's own folder finds all "
         "downloaded lists across its version directories "
         "(&lt;install&gt;\\&lt;version&gt;\\downloaded_mod_lists). Folder "
         "finds load implicitly (latest-only filterable); add a specific "
         "<i>file</i> to pin that exact version so no filter drops it."),
        ("nolvus", "Nolvus", "Nolvus manifests (*.xml *.xml.gz)", True,
         "Nolvus installer manifests (InstallPackage.xml / .xml.gz). This "
         "project bundles them under manifests/nolvus - add that folder "
         "once and new guide versions arrive with app updates. Adding a "
         "specific file pins that guide version."),
        ("installs", "Installs", None, True,
         "MO2 installations checked for [NoDelete]-prefixed custom "
         "additions: each such mod's source archive is protected in the "
         "downloads folder. Add one install (the folder containing mods/) "
         "or a parent folder whose direct children are installs. Installs "
         "without [NoDelete] mods contribute nothing and are skipped."),
        ("recovery", "Recovery", None, True,
         "Fallback for lists whose .wabbajack manifest is gone: every "
         "archive the install's mods were made from is whitelisted by NAME "
         "only. Weaker than a real manifest - it cannot tell versions "
         "apart beyond the file name - so prefer keeping the .wabbajack "
         "or a snapshot."),
        ("snapshots", "Snapshots", "Snapshots (*.json)", False,
         "Compact whitelists exported by `modsweep snapshot`. A snapshot "
         "classifies identically to the manifest it came from and survives "
         "that manifest's deletion - cheap insurance before uninstalling "
         "Wabbajack."),
    )
    EXCLUDE_DESC = (
        "Retire lists without touching any files: case-insensitive globs "
        "matched against the list label ('LoreRim 2.2*') or the manifest "
        "file name. Tip: unchecking a source in the main window manages "
        "exact-label entries here for you."
    )

    def __init__(self, cfg: config.Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit modsweep config")
        self.resize(760, 560)
        self._cache = cfg.cache  # not edited here; preserved through saves

        self.downloads_edit = QLineEdit(str(cfg.downloads or ""))
        self.quarantine_edit = QLineEdit(str(cfg.quarantine or ""))
        self.keep_days = QSpinBox()
        self.keep_days.setRange(0, 3650)
        self.keep_days.setValue(
            cfg.quarantine_keep_days if cfg.quarantine_keep_days is not None else 30
        )
        self.keep_days.setToolTip("purge deletes quarantine batches older than this")
        self.latest_only = QCheckBox("Keep only the newest version of each list")
        self.latest_only.setChecked(cfg.latest_only)
        self.latest_only.setToolTip(
            "Explicitly listed files are pinned and survive this filter"
        )

        form = QFormLayout()
        form.addRow("Downloads folder:", self._with_browse(self.downloads_edit))
        form.addRow("Quarantine folder:", self._with_browse(self.quarantine_edit))
        form.addRow("Purge after (days):", self.keep_days)
        form.addRow("", self.latest_only)

        self.editors: dict[str, PathListEditor] = {}
        tabs = QTabWidget()
        for key, title, file_filter, allow_dirs, desc in self.SOURCE_TABS:
            editor = PathListEditor(getattr(cfg, key), file_filter, allow_dirs)
            self.editors[key] = editor
            tabs.addTab(self._described_page(desc, editor), title)
        self.exclude_editor = PathListEditor(cfg.exclude, None, False, text_only=True)
        tabs.addTab(self._described_page(self.EXCLUDE_DESC, self.exclude_editor), "Exclude")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Help
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(self._show_help)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Sources of truth</b>"))
        layout.addWidget(tabs, 1)
        layout.addWidget(buttons)

    @staticmethod
    def _described_page(description: str, editor: PathListEditor) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(description)
        label.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(editor, 1)
        return page

    def _show_help(self) -> None:  # pragma: no cover - modal dialog
        QMessageBox.information(self, "Source resolution & retirement", RESOLUTION_HELP)

    def _with_browse(self, edit: QLineEdit) -> QWidget:
        browse = QPushButton("Browse...")

        def pick() -> None:  # pragma: no cover - native dialog
            chosen = QFileDialog.getExistingDirectory(self, "Choose folder")
            if chosen:
                edit.setText(chosen)

        browse.clicked.connect(pick)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(edit, 1)
        row_layout.addWidget(browse)
        return row

    def result_config(self) -> config.Config:
        def path_or_none(text: str) -> Path | None:
            text = text.strip()
            return Path(text) if text else None

        return config.Config(
            downloads=path_or_none(self.downloads_edit.text()),
            cache=self._cache,
            wabbajack=[Path(v) for v in self.editors["wabbajack"].values()],
            nolvus=[Path(v) for v in self.editors["nolvus"].values()],
            installs=[Path(v) for v in self.editors["installs"].values()],
            recovery=[Path(v) for v in self.editors["recovery"].values()],
            snapshots=[Path(v) for v in self.editors["snapshots"].values()],
            exclude=self.exclude_editor.values(),
            latest_only=self.latest_only.isChecked(),
            quarantine=path_or_none(self.quarantine_edit.text()),
            quarantine_keep_days=self.keep_days.value(),
        )


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
        buffer = io.StringIO()
        try:
            with redirect_stderr(buffer):
                self._fn(self)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            for announced in buffer.getvalue().splitlines():
                self.line.emit(announced)


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path | None = None, show_welcome: bool = True):
        super().__init__()
        self.setWindowTitle("modsweep")
        self.setWindowIcon(_app_icon())
        self.resize(1200, 750)
        self._worker: Worker | None = None

        self.show_result_popups = True
        self.sources_list = QListWidget()
        self.sources_list.setAlternatingRowColors(True)
        self.sources_list.setToolTip(
            "Every checked source protects the files it names.\n"
            "Untick a list to retire it (Apply Selection writes an exclude "
            "for you); tick an excluded one to reinstate it.\n\n" + RESOLUTION_HELP
        )
        self.sources_list.itemChanged.connect(self._on_source_toggled)
        self._suppress_source_signal = False

        self._build_buttons()
        self._build_tabs()
        self.progress = QProgressBar()
        self.progress.hide()

        bar = QHBoxLayout()
        for button in self.buttons.values():
            bar.addWidget(button)
        bar.addStretch()

        header = QLabel("<b>Active sources</b>")
        header.setToolTip(RESOLUTION_HELP)

        self.select_all_btn = QPushButton("All")
        self.select_none_btn = QPushButton("None")
        self.apply_selection_btn = QPushButton("Apply Selection")
        self.select_all_btn.setToolTip("Check every selectable source")
        self.select_none_btn.setToolTip("Uncheck every selectable source")
        self.apply_selection_btn.setToolTip(
            "Write the checkbox choices to the config as exact-label "
            "excludes, then reload"
        )
        self.select_all_btn.clicked.connect(lambda: self._set_all_sources(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all_sources(False))
        self.apply_selection_btn.clicked.connect(self.apply_source_selection)
        self.apply_selection_btn.setEnabled(False)

        selection_bar = QHBoxLayout()
        selection_bar.addWidget(self.select_all_btn)
        selection_bar.addWidget(self.select_none_btn)
        selection_bar.addStretch()
        selection_bar.addWidget(self.apply_selection_btn)

        sources_pane = QWidget()
        sources_layout = QVBoxLayout(sources_pane)
        sources_layout.setContentsMargins(0, 0, 0, 0)
        sources_layout.addWidget(header)
        sources_layout.addWidget(self.sources_list)
        sources_layout.addLayout(selection_bar)

        splitter = QSplitter()
        splitter.addWidget(sources_pane)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(1, 3)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(bar)
        layout.addWidget(splitter)
        layout.addWidget(self.progress)
        self.setCentralWidget(root)

        self.log("Tip: hover any button for guidance; action output lands here.")
        self._log_handler = GuiLogHandler()
        pipeline_logger = logging.getLogger("modsweep")
        pipeline_logger.addHandler(self._log_handler)
        if pipeline_logger.level in (logging.NOTSET, logging.WARNING):
            pipeline_logger.setLevel(logging.INFO)  # timings surface in the Log

        self._last_results: dict[str, object] = {}
        self.config_path = config_path
        self.cfg = config.load(config_path)
        self.status_config = QLabel()
        self.statusBar().addPermanentWidget(self.status_config)
        self._show_config_status()
        if show_welcome:
            self._maybe_show_welcome()
        self.refresh_sources()

    # --- construction -------------------------------------------------------

    def _build_buttons(self) -> None:
        self.buttons = {
            "open": QPushButton("Open Config..."),
            "edit": QPushButton("Edit Config..."),
            "refresh": QPushButton("Refresh Sources"),
            "report": QPushButton("Report"),
            "hash": QPushButton("Hash Candidates"),
            "dry": QPushButton("Sweep (Dry Run)"),
            "apply": QPushButton("Sweep + Apply..."),
            "restore": QPushButton("Restore..."),
            "purge": QPushButton("Purge..."),
            "snapshot": QPushButton("Snapshot..."),
        }
        for name, button in self.buttons.items():
            button.setToolTip(TOOLTIPS[name])
        self.buttons["open"].clicked.connect(self.open_config)
        self.buttons["edit"].clicked.connect(self.edit_config)
        self.buttons["refresh"].clicked.connect(self.refresh_sources)
        self.buttons["report"].clicked.connect(self.run_report)
        self.buttons["hash"].clicked.connect(self.run_hash_candidates)
        self.buttons["dry"].clicked.connect(lambda: self.run_sweep(apply=False))
        self.buttons["apply"].clicked.connect(lambda: self.run_sweep(apply=True))
        self.buttons["restore"].clicked.connect(lambda: self.run_restore())
        self.buttons["purge"].clicked.connect(lambda: self.run_purge())
        self.buttons["snapshot"].clicked.connect(lambda: self.run_snapshot())

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
        self.candidates_table.setToolTip(
            "Right-click a row: open in your file manager, quarantine just "
            "that file, or delete it. Hover a status for what it means."
        )
        self.candidates_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.candidates_table.customContextMenuRequested.connect(
            self._candidates_menu
        )

        report_tab = QWidget()
        report_layout = QVBoxLayout(report_tab)
        report_layout.addWidget(self.summary_label)
        report_layout.addWidget(self.status_table, 2)
        report_layout.addWidget(QLabel("<b>Disk archives claimed per source</b>"))
        report_layout.addWidget(self.claims_table, 4)
        report_layout.addWidget(QLabel("<b>Deletion candidates</b>"))
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

    def _maybe_show_welcome(self) -> None:  # pragma: no cover - modal dialog
        settings = QSettings("modsweep", "modsweep")
        if settings.value("welcome/suppressed", False, type=bool):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to modsweep")
        box.setText(WELCOME)
        suppress = QCheckBox("Don't show this again")
        box.setCheckBox(suppress)
        box.exec()
        if suppress.isChecked():
            settings.setValue("welcome/suppressed", True)

    # --- config / sources -----------------------------------------------------

    def _show_config_status(self) -> None:
        shown = self.config_path or Path(config.DEFAULT_NAME)
        self.status_config.setText(
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

    def edit_config(self) -> None:  # pragma: no cover - modal dialog
        dialog = ConfigEditorDialog(self.cfg, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.apply_config(dialog.result_config())

    def apply_config(self, new_cfg: config.Config) -> None:
        """Persist an edited config, reload it, and refresh the sources."""
        target = self.config_path or Path(config.DEFAULT_NAME)
        config.save(new_cfg, target)
        self.config_path = Path(target)
        self.cfg = config.load(self.config_path)
        self._show_config_status()
        self._on_status(f"Config saved to {target}.")
        self.refresh_sources()

    def _load_active(self, worker: Worker) -> list[Manifest]:
        """Resolve sources (parsing manifests is the slow part - workers only)."""
        manifests = load_manifests(
            config_sources(self.cfg),
            self.cfg.exclude,
            self.cfg.latest_only,
            self._parse_cache_dir(),
        )
        self._check_drift(worker, manifests)
        return manifests

    def _parse_cache_dir(self) -> Path:
        return self._cache_path().parent / "manifest_cache"

    def refresh_sources(self) -> None:
        def action(worker: Worker) -> None:
            infos = survey_sources(
                config_sources(self.cfg),
                self.cfg.exclude,
                self.cfg.latest_only,
                self._parse_cache_dir(),
            )
            actives = [
                i.manifest for i in infos if i.state in ("active", "pinned")
            ]
            self._check_drift(worker, actives)
            worker.payload.emit(("sources", infos))
            worker.status.emit(f"{len(actives)} active source(s) loaded.")

        self._start(action, "Loading sources")

    def _check_drift(self, worker: Worker, manifests: list[Manifest]) -> None:
        state_path = self._cache_path().parent / state.STATE_NAME
        for label, source in state.vanished(state.read(state_path), manifests):
            worker.line.emit(
                f"warning: previously-active source vanished: {label} "
                f"({source} no longer exists)"
            )
        state.write(state_path, manifests)

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
            worker.status.emit(
                f"Report updated - potential reclaim {_gb(reclaim_bytes(results))}."
            )

        self._start(action, "Building report")

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
                for i, disk in enumerate(pending, 1):
                    xxh64_b64, crc32 = hash_file(disk.path)
                    cache.put(disk, xxh64_b64, crc32)
                    worker.progress.emit(i, len(pending))
            finally:
                cache.close()
            worker.summary.emit(
                f"Hashing done - {len(pending):,} candidate file(s) hashed."
            )

        self._start(action, "Hashing candidates", then_report=True)

    def run_sweep(self, apply: bool) -> None:
        if apply:  # pragma: no cover - native dialog
            answer = QMessageBox.question(
                self,
                "Apply sweep?",
                "Move all candidates to quarantine?\n\n"
                "Fully reversible with the Restore button.",
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
                worker.summary.emit(
                    f"Dry run: {len(plan.ready):,} files / "
                    f"{_gb(plan.ready_bytes)} would be quarantined"
                    + (f"; {len(plan.refused):,} refused (unhashed)" if plan.refused else "")
                    + "."
                )
                return
            if not plan.ready:
                worker.summary.emit("Nothing to move - no eligible candidates.")
                return
            batch = sweep_mod.execute(plan, quarantine)
            worker.summary.emit(
                f"Moved {len(plan.ready):,} files ({_gb(plan.ready_bytes)}) "
                f"to {batch.name} - restorable."
            )

        self._start(action, "Sweeping", then_report=apply)

    def run_restore(self, batch: Path | None = None) -> None:
        if batch is None:  # pragma: no cover - native dialog
            batch = self._pick_batch("Restore which quarantine batch?")
            if batch is None:
                return

        def action(worker: Worker) -> None:
            moved, skipped, missing = sweep_mod.restore(batch)
            message = f"Restored {moved:,} files from {Path(batch).name}."
            if skipped:
                message += f" {skipped:,} skipped (original path occupied)."
            if missing:
                message += f" {missing:,} missing from the batch."
            worker.summary.emit(message)

        self._start(action, "Restoring", then_report=True)

    def run_purge(self, batch: Path | None = None) -> None:
        if batch is None:  # pragma: no cover - native dialog
            batch = self._pick_batch("Purge which quarantine batch?")
            if batch is None:
                return
        described = self._describe_batch(Path(batch))
        answer = QMessageBox.warning(
            self,
            "Permanently delete batch?",
            f"PERMANENTLY delete this quarantine batch?\n\n{described}"
            f"{self._trust_period_note(Path(batch))}\n\n"
            "This is the only unrecoverable action in modsweep.\n"
            "There is NO undo.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def action(worker: Worker) -> None:
            sweep_mod.purge_batch(Path(batch))
            worker.summary.emit(f"Purged {Path(batch).name} - permanently deleted.")

        self._start(action, "Purging")

    # --- per-file actions (candidates context menu) ------------------------

    def _candidates_menu(self, pos) -> None:  # pragma: no cover - native menu
        item = self.candidates_table.itemAt(pos)
        if item is None:
            return
        rel = self.candidates_table.item(item.row(), 2).text()
        menu = QMenu(self)
        menu.addAction("Open in file manager", lambda: self._reveal(rel))
        menu.addSeparator()
        menu.addAction("Quarantine this file", lambda: self.quarantine_file(rel))
        menu.addAction("Delete this file...", lambda: self.delete_file(rel))
        menu.exec(self.candidates_table.viewport().mapToGlobal(pos))

    def _reveal(self, rel: str) -> None:  # pragma: no cover - launches OS shell
        path = Path(self.cfg.downloads) / rel
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def quarantine_file(self, rel: str, purge: bool = False) -> None:
        subset = self._results_for(rel)
        if not subset:
            self._on_status(f"error: {rel} is not in the last report - run Report first")
            return
        if purge:
            answer = QMessageBox.warning(
                self,
                "Permanently delete file?",
                f"PERMANENTLY delete this file (and its .meta sidecar)?\n\n"
                f"{rel}\n\nQuarantined and immediately purged - there is NO undo.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        def action(worker: Worker) -> None:
            cache = HashCache(self._cache_path())
            try:
                plan = sweep_mod.plan(subset, cache)
            finally:
                cache.close()
            if plan.refused:
                worker.summary.emit(
                    f"Refused: {rel} has no verified hash - run Hash Candidates first."
                )
                return
            if not plan.ready:
                worker.summary.emit(f"{rel} is not an eligible candidate.")
                return
            quarantine = _quarantine_dir_for(self.cfg.downloads, self.cfg.quarantine)
            batch = sweep_mod.execute(plan, quarantine, tag="file")
            if purge:
                sweep_mod.purge_batch(batch)
                worker.summary.emit(f"Deleted {rel} - permanently.")
            else:
                worker.summary.emit(
                    f"Quarantined {rel} -> {batch.name} (restorable)."
                )

        self._start(action, "Quarantining file", then_report=True)

    def delete_file(self, rel: str) -> None:
        self.quarantine_file(rel, purge=True)

    def _results_for(self, rel: str) -> list:
        """The stored FileResult for rel, plus its .meta sidecar if present."""
        subset = []
        for key in (rel, rel + ".meta"):
            result = self._last_results.get(key)
            if result is not None:
                subset.append(result)
        return subset

    def run_snapshot(self, out_dir: Path | None = None) -> None:
        if out_dir is None:  # pragma: no cover - native dialog
            base = self.config_path.parent if self.config_path else Path(".")
            chosen = QFileDialog.getExistingDirectory(
                self, "Snapshot output folder", str(base)
            )
            if not chosen:
                return
            out_dir = Path(chosen)

        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            for m in manifests:
                path = snapshot_mod.save(m, out_dir)
                worker.line.emit(f"  {m.label} -> {path.name}")
            worker.summary.emit(
                f"{len(manifests)} snapshot(s) written to {out_dir} - these "
                f"whitelists outlive their original manifests."
            )

        self._start(action, "Exporting snapshots")

    def _quarantine_batches(self) -> list[sweep_mod.Batch]:
        quarantine = _quarantine_dir_for(self.cfg.downloads, self.cfg.quarantine)
        return sweep_mod.list_batches(quarantine)

    def _find_batch(self, batch: Path) -> sweep_mod.Batch | None:
        return next((b for b in self._quarantine_batches() if b.path == batch), None)

    def _describe_batch(self, batch: Path) -> str:
        b = self._find_batch(batch)
        if b is None:
            return str(batch)
        age = (datetime.now() - b.created).days
        return f"{b.path.name}  ({b.files:,} files, {_gb(b.size)}, {age}d old)"

    def _trust_period_note(self, batch: Path) -> str:
        b = self._find_batch(batch)
        if b is None:
            return ""
        keep_days = (
            self.cfg.quarantine_keep_days
            if self.cfg.quarantine_keep_days is not None
            else 30
        )
        age = (datetime.now() - b.created).days
        if age >= keep_days:
            return ""
        return (
            f"\n\nNote: this batch is only {age}d old - younger than the "
            f"{keep_days}-day trust period. (keep_days guides the CLI's "
            f"age-based purge; this button purges whatever you pick.)"
        )

    def _pick_batch(self, title: str) -> Path | None:  # pragma: no cover - dialog
        batches = self._quarantine_batches()
        if not batches:
            QMessageBox.information(self, "No batches", "The quarantine is empty.")
            return None
        labels = [self._describe_batch(b.path) for b in batches]
        chosen, ok = QInputDialog.getItem(
            self, "modsweep", title, labels, len(labels) - 1, False
        )
        if not ok:
            return None
        return batches[labels.index(chosen)].path

    # --- rendering ---------------------------------------------------------------

    def _on_payload(self, message: object) -> None:
        kind, data = message
        if kind == "sources":
            self._show_sources(data)
        elif kind == "report":
            manifests, results = data
            self._show_report(manifests, results)

    def _show_sources(self, infos: list[SourceInfo]) -> None:
        self._suppress_source_signal = True
        self.sources_list.clear()
        for info in infos:
            m = info.manifest
            item = QListWidgetItem(f"{m.label}  ({len(m.entries)} entries)")
            item.setData(Qt.ItemDataRole.UserRole, (m.label, info.state, info.detail))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if info.state in ("active", "pinned"):
                item.setCheckState(Qt.CheckState.Checked)
                if info.state == "pinned":
                    item.setToolTip(
                        f"Pinned: named explicitly in the config, kept "
                        f"despite {info.detail}"
                    )
                else:
                    item.setToolTip("Active - untick to retire this list")
            elif info.state == "excluded":
                item.setCheckState(Qt.CheckState.Unchecked)
                if is_exact_exclude(info.detail, m.label):
                    item.setToolTip("Excluded - tick to reinstate")
                else:
                    self._lock_item(item, f"excluded by '{info.detail}'")
                    item.setToolTip(
                        f"Excluded by the pattern '{info.detail}' - manage "
                        f"it under Edit Config > Exclude"
                    )
            else:  # superseded by latest-only
                item.setCheckState(Qt.CheckState.Unchecked)
                self._lock_item(item, "locked by latest_only")
                item.setToolTip(
                    f"latest_only is locking this version out: {info.detail} "
                    f"supersedes it. To keep this version anyway, add its "
                    f"manifest file explicitly under Edit Config (any tab's "
                    f"Add File...) - explicitly named files are pinned and "
                    f"survive the filter."
                )
            self.sources_list.addItem(item)
        self._suppress_source_signal = False
        self.apply_selection_btn.setEnabled(False)

    @staticmethod
    def _lock_item(item: QListWidgetItem, reason: str) -> None:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        item.setText(f"{item.text()}  - {reason}")
        font = item.font()
        font.setItalic(True)
        item.setFont(font)

    def _on_source_toggled(self, _item) -> None:
        if not self._suppress_source_signal:
            self.apply_selection_btn.setEnabled(True)

    def _set_all_sources(self, checked: bool) -> None:
        target = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.sources_list.count()):
            item = self.sources_list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(target)

    def apply_source_selection(self) -> None:
        """Translate the checkboxes into exact-label excludes and reload."""
        exclude = list(self.cfg.exclude)
        for i in range(self.sources_list.count()):
            item = self.sources_list.item(i)
            if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
                continue
            label, source_state, _detail = item.data(Qt.ItemDataRole.UserRole)
            checked = item.checkState() == Qt.CheckState.Checked
            if source_state in ("active", "pinned") and not checked:
                pattern = exact_exclude_pattern(label)
                if not any(is_exact_exclude(e, label) for e in exclude):
                    exclude.append(pattern)
            elif source_state == "excluded" and checked:
                exclude = [e for e in exclude if not is_exact_exclude(e, label)]
        self.apply_config(replace(self.cfg, exclude=exclude))

    def _show_report(self, manifests: list[Manifest], results) -> None:
        self._last_results = {r.disk.rel: r for r in results}
        total_files = len(results)
        total_bytes = sum(r.disk.size for r in results)
        self.summary_label.setText(
            f"<b>{total_files:,}</b> files / <b>{_gb(total_bytes)}</b> across "
            f"<b>{len(manifests)}</b> sources &nbsp;|&nbsp; potential reclaim: "
            f"<b>{_gb(reclaim_bytes(results))}</b>"
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
        candidate_items = []
        for size, status, rel in candidate_rows(results):
            status_item = QTableWidgetItem(status)
            status_item.setToolTip(STATUS_HELP.get(status, status))
            candidate_items.append(
                [NumericItem(size, _gb(size)), status_item, QTableWidgetItem(rel)]
            )
        self._fill(self.candidates_table, candidate_items)
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

    def _on_status(self, text: str) -> None:
        self.statusBar().showMessage(text, 30_000)
        self.log(text)

    def _on_summary(self, text: str) -> None:
        """Action results: status bar + log, plus a popup so the outcome is
        unmissable even when the user is watching another tab."""
        self._on_status(text)
        if self.show_result_popups:
            QMessageBox.information(self, "modsweep", text)

    def _cache_path(self) -> Path:
        return self.cfg.cache or DEFAULT_CACHE

    def _start(self, fn, doing: str, then_report: bool = False) -> None:
        if self._worker is not None and self._worker.isRunning():
            return  # one action at a time
        self._set_busy(True, doing)
        self._worker = Worker(fn, parent=self)
        self._worker.line.connect(self.log)
        self._worker.status.connect(self._on_status)
        self._worker.summary.connect(self._on_summary)
        self._worker.progress.connect(self._on_progress)
        self._worker.payload.connect(self._on_payload)
        self._worker.failed.connect(lambda msg: self._on_status(f"error: {msg}"))
        self._worker.finished.connect(lambda: self._on_finished(then_report))
        self._worker.start()

    def _on_finished(self, then_report: bool) -> None:
        for record in self._log_handler.drain():
            self.log(record)
        self._set_busy(False, "")
        if then_report:
            self.run_report()

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)  # back to indeterminate
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _set_busy(self, busy: bool, doing: str) -> None:
        for name, button in self.buttons.items():
            if name != "open":
                button.setEnabled(not busy)
        if busy:
            # Indeterminate until an action reports concrete progress, so the
            # window never looks frozen while a worker runs.
            self.progress.setRange(0, 0)
            self.progress.show()
            self.statusBar().showMessage(f"{doing}...")
        else:
            self.progress.hide()


def main() -> int:  # pragma: no cover - event loop
    app = QApplication(sys.argv)
    app.setApplicationName("modsweep")
    app.setWindowIcon(_app_icon())
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    window = MainWindow(config_path)
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
