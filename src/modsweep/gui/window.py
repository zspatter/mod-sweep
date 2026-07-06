"""The Mod Sweep main window: buttons, source tree, report tables."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, config, remote, state
from .. import snapshot as snapshot_mod
from .. import sweep as sweep_mod
from ..cache import HashCache
from ..cli import (
    DEFAULT_CACHE,
    SourceInfo,
    _infer_file_kind,
    _quarantine_dir_for,
    config_sources,
    exact_exclude_pattern,
    is_exact_exclude,
    load_manifests,
    survey_sources,
)
from ..hashutil import hash_file
from ..manifest import Manifest, version_key
from ..matcher import STALE, UNCLAIMED, match
from ..report import candidate_rows, claim_rows, reclaim_bytes, status_rows
from ..scanner import scan
from .editor import ConfigEditorDialog
from .icons import _app_icon, _ban_icon, _lock_icon, _pin_icon
from .texts import RESOLUTION_HELP, STATUS_HELP, TOOLTIPS, WELCOME
from .workers import GuiLogHandler, LogBridge, Worker


def _gb(size: float) -> str:
    return f"{size / (1 << 30):,.2f} GB"


# config keys that pin a manifest file kind when listed explicitly
_PIN_KEYS = {"wabbajack": "wabbajack", "nolvus": "nolvus", "snapshot": "snapshots"}


class NumericItem(QTableWidgetItem):
    """Table cell that displays formatted text but sorts by a numeric value."""

    def __init__(self, value: float, text: str):
        super().__init__(text)
        self._value = value

    def __lt__(self, other) -> bool:
        if isinstance(other, NumericItem):
            return self._value < other._value
        return super().__lt__(other)


class MainWindow(QMainWindow):
    def __init__(self, config_path: Path | None = None, show_welcome: bool = True):
        super().__init__()
        self.setWindowTitle("Mod Sweep")
        self.setWindowIcon(_app_icon())
        self.resize(1200, 750)
        self._worker: Worker | None = None

        self.show_result_popups = True
        self.sources_list = QTreeWidget()
        self.sources_list.setHeaderHidden(True)
        self.sources_list.setAlternatingRowColors(True)
        self.sources_list.setToolTip(
            "Every checked source protects the files it names.\n"
            "Untick a list to retire it (Apply Selection writes an exclude "
            "for you); tick an excluded one to reinstate it.\n"
            "Lists with several versions group under their newest - expand "
            "a row to reach older versions.\n\n" + RESOLUTION_HELP
        )
        self.sources_list.itemChanged.connect(self._on_source_toggled)
        self.sources_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sources_list.customContextMenuRequested.connect(self._sources_menu)
        self._suppress_source_signal = False
        self._last_infos: dict[str, SourceInfo] = {}
        self._all_infos: list[SourceInfo] = []

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

        self._build_menus()
        self.log("Tip: hover any button for guidance; action output lands here.")
        self._log_bridge = LogBridge()
        self._log_bridge.message.connect(self.log)
        self._log_handler = GuiLogHandler(self._log_bridge)
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

    def _build_menus(self) -> None:
        tools = self.menuBar().addMenu("&Tools")
        tools.addAction("Update Nolvus Manifests...", self.run_manifest_update)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("Check for Updates...", self.run_update_check)
        help_menu.addAction(
            "Source Resolution && Retirement...",
            lambda: QMessageBox.information(  # pragma: no cover - modal
                self, "Source resolution & retirement", RESOLUTION_HELP
            ),
        )

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

    def _maybe_show_welcome(self) -> None:
        settings = QSettings("modsweep", "modsweep")
        if settings.value("welcome/suppressed", False, type=bool):
            return
        box = QMessageBox(self)
        box.setWindowTitle("Welcome to Mod Sweep")
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

    def open_config(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Open Mod Sweep config", "", "TOML (*.toml)"
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

    def _require_downloads(self) -> Path | None:
        """Friendly gate for actions that need a downloads dir configured."""
        if self.cfg.downloads is None:
            self._on_status(
                "error: no downloads directory configured - set it via Edit Config"
            )
            return None
        return Path(self.cfg.downloads)

    def run_report(self) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return

        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            files = scan(downloads)
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
        downloads = self._require_downloads()
        if downloads is None:
            return

        def action(worker: Worker) -> None:
            manifests = self._load_active(worker)
            all_files = scan(downloads)
            files = [f for f in all_files if not f.is_meta]
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
                # One scan serves both phases: re-match with the fresh
                # hashes (sidecars included) and deliver the report here
                # instead of chaining a second scan-and-match worker.
                results = match(all_files, manifests, cache)
            finally:
                cache.close()
            worker.payload.emit(("report", (manifests, results)))
            worker.summary.emit(
                f"Hashing done - {len(pending):,} candidate file(s) hashed."
            )

        self._start(action, "Hashing candidates")

    def run_sweep(self, apply: bool) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return
        if apply:
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
            quarantine = _quarantine_dir_for(downloads, self.cfg.quarantine)
            files = scan(downloads)
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
        if self._require_downloads() is None:
            return
        if batch is None:
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
        if self._require_downloads() is None:
            return
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
            "This is the only unrecoverable action in Mod Sweep.\n"
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
        rel_item = self.candidates_table.item(item.row(), 2)
        if rel_item is None:
            return
        rel = rel_item.text()
        menu = QMenu(self)
        menu.addAction("Open in file manager", lambda: self._reveal(rel))
        menu.addSeparator()
        menu.addAction("Quarantine this file", lambda: self.quarantine_file(rel))
        menu.addAction("Delete this file...", lambda: self.delete_file(rel))
        menu.exec(self.candidates_table.viewport().mapToGlobal(pos))

    def _reveal(self, rel: str) -> None:  # pragma: no cover - launches OS shell
        downloads = self._require_downloads()
        if downloads is not None:
            self._reveal_path(downloads / rel)

    @staticmethod
    def _reveal_path(path: Path) -> None:  # pragma: no cover - launches OS shell
        if sys.platform == "win32":
            subprocess.Popen(["explorer", "/select,", str(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def quarantine_file(self, rel: str, purge: bool = False) -> None:
        downloads = self._require_downloads()
        if downloads is None:
            return
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
            quarantine = _quarantine_dir_for(downloads, self.cfg.quarantine)
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

    def run_manifest_update(self) -> None:
        def action(worker: Worker) -> None:
            downloaded = remote.update_manifests()
            if downloaded:
                worker.summary.emit(
                    f"Downloaded {len(downloaded)} new manifest(s): "
                    + ", ".join(downloaded)
                )
            else:
                worker.summary.emit("Bundled manifests are up to date.")

        self._start(action, "Updating manifests", then_refresh=True)

    def run_update_check(self) -> None:
        def action(worker: Worker) -> None:
            info = remote.check_update(__version__)
            if info is None:
                worker.summary.emit(f"Mod Sweep v{__version__} is up to date.")
            else:
                worker.status.emit(f"Update available: v{info.latest}")
                worker.payload.emit(("update", info))

        self._start(action, "Checking for updates")

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
        if self.cfg.downloads is None:  # nowhere to restore to anyway
            return []
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

    def _pick_batch(self, title: str) -> Path | None:
        batches = self._quarantine_batches()
        if not batches:
            QMessageBox.information(self, "No batches", "The quarantine is empty.")
            return None
        labels = [self._describe_batch(b.path) for b in batches]
        chosen, ok = QInputDialog.getItem(
            self, "Mod Sweep", title, labels, len(labels) - 1, False
        )
        if not ok:
            return None
        return batches[labels.index(chosen)].path

    # --- rendering ---------------------------------------------------------------

    def _on_payload(self, message: object) -> None:
        kind, data = cast("tuple[str, Any]", message)
        if kind == "sources":
            self._show_sources(data)
        elif kind == "report":
            manifests, results = data
            self._show_report(manifests, results)
        elif kind == "update":
            self._offer_update(data)

    def _offer_update(self, info: remote.UpdateInfo) -> None:
        answer = QMessageBox.question(
            self,
            "Update available",
            f"Mod Sweep v{info.latest} is available (you are running "
            f"v{info.current}).\n\nOpen the releases page?",
        )
        if answer == QMessageBox.StandardButton.Yes:  # pragma: no cover - browser
            QDesktopServices.openUrl(QUrl(info.url))

    def _show_sources(self, infos: list[SourceInfo]) -> None:
        self._all_infos = infos
        self._last_infos = {i.manifest.label: i for i in infos}
        self._render_sources()

    def _render_sources(self) -> None:
        """One row per list, newest version as the parent, older versions as
        children. Groups collapse unless a child carries a user decision
        (pin/exclusion), so routine version churn never floods the list."""
        groups: dict[str, list[SourceInfo]] = {}
        for info in self._all_infos:
            groups.setdefault(info.manifest.group_key, []).append(info)

        self._suppress_source_signal = True
        self.sources_list.clear()
        for key in sorted(groups):
            members = sorted(
                groups[key],
                key=lambda i: version_key(i.manifest.version),
                reverse=True,
            )
            newest, *older = members
            parent = self._source_item(newest)
            for info in older:
                parent.addChild(self._source_item(info))
            if older:
                parent.setText(0, parent.text(0) + f"  [+{len(older)} older]")
            self.sources_list.addTopLevelItem(parent)
            if older:
                parent.setExpanded(any(i.state != "superseded" for i in older))
        self._suppress_source_signal = False
        self.apply_selection_btn.setEnabled(False)

    def _iter_source_items(self) -> Iterator[QTreeWidgetItem]:
        for i in range(self.sources_list.topLevelItemCount()):
            top = self.sources_list.topLevelItem(i)
            if top is None:  # pragma: no cover - Qt guarantees the range
                continue
            yield top
            for j in range(top.childCount()):
                child = top.child(j)
                if child is not None:
                    yield child

    def _source_item(self, info: SourceInfo) -> QTreeWidgetItem:
        m = info.manifest
        item = QTreeWidgetItem([f"{m.label}  ({len(m.entries)} entries)"])
        item.setData(0, Qt.ItemDataRole.UserRole, (m.label, info.state, info.detail))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        if info.state in ("active", "pinned"):
            item.setCheckState(0, Qt.CheckState.Checked)
            if info.state == "pinned":
                item.setIcon(0, _pin_icon())
                reason = (
                    f"kept despite {info.detail}"
                    if info.detail
                    else "never dropped by latest_only"
                )
                item.setToolTip(0, f"Pinned: named explicitly in the config - {reason}")
            else:
                item.setToolTip(0, "Active - untick to retire this list")
        elif info.state == "excluded":
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setIcon(0, _ban_icon())
            if is_exact_exclude(info.detail, m.label):
                item.setToolTip(0, "Excluded - tick to reinstate")
            else:
                self._lock_item(item, f"excluded by '{info.detail}'")
                item.setToolTip(
                    0,
                    f"Excluded by the pattern '{info.detail}' - manage "
                    f"it under Edit Config > Exclude",
                )
        else:  # superseded by latest-only
            item.setCheckState(0, Qt.CheckState.Unchecked)
            item.setIcon(0, _lock_icon())
            self._lock_item(item, "locked by latest_only")
            item.setToolTip(
                0,
                f"latest_only is locking this version out: {info.detail} "
                f"supersedes it. Right-click to pin this version - pinned "
                f"files survive the filter.",
            )
        return item

    @staticmethod
    def _lock_item(item: QTreeWidgetItem, reason: str) -> None:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        item.setText(0, f"{item.text(0)}  - {reason}")
        font = item.font(0)
        font.setItalic(True)
        item.setFont(0, font)

    def _on_source_toggled(self, _item, _column: int = 0) -> None:
        if not self._suppress_source_signal:
            self.apply_selection_btn.setEnabled(True)

    def _set_all_sources(self, checked: bool) -> None:
        target = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._iter_source_items():
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(0, target)

    def _sources_menu(self, pos) -> None:  # pragma: no cover - native menu
        item = self.sources_list.itemAt(pos)
        if item is None:
            return
        label, source_state, detail = item.data(0, Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        info = self._last_infos.get(label)
        pinnable = (
            info is not None
            and _infer_file_kind(info.manifest.source_path) in _PIN_KEYS
        )
        if source_state in ("active", "superseded") and pinnable:
            menu.addAction(
                "Pin this version (survives latest_only)",
                lambda: self.pin_source(label),
            )
        if source_state == "pinned" and pinnable:
            menu.addAction(
                "Unpin (remove explicit entry)", lambda: self.unpin_source(label)
            )
        if source_state in ("active", "pinned"):
            menu.addAction(
                "Retire this list (exclude)", lambda: self.retire_source(label)
            )
        if source_state == "excluded" and is_exact_exclude(detail, label):
            menu.addAction("Reinstate", lambda: self.reinstate_source(label))
        menu.addSeparator()
        menu.addAction(
            "Open manifest location",
            lambda: self._reveal_path(self._last_infos[label].manifest.source_path),
        )
        menu.exec(self.sources_list.viewport().mapToGlobal(pos))

    def pin_source(self, label: str) -> None:
        """Add the source's manifest file as an explicit (pinned) config entry."""
        info = self._last_infos.get(label)
        if info is None:
            self._on_status(f"error: {label} is not in the current source list")
            return
        path = info.manifest.source_path
        kind = _infer_file_kind(path)
        key = _PIN_KEYS.get(kind or "")
        if key is None:
            self._on_status(f"error: cannot pin {label}: {path.name} is not a "
                            f"pinnable manifest file")
            return
        values = getattr(self.cfg, key)
        if path in values:
            self._on_status(f"{label} is already pinned.")
            return
        self.apply_config(replace(self.cfg, **{key: [*values, path]}))

    def unpin_source(self, label: str) -> None:
        """Remove the explicit config entry; the source stays only if a
        directory walk still finds it (and latest_only may then drop it)."""
        info = self._last_infos.get(label)
        if info is None:
            self._on_status(f"error: {label} is not in the current source list")
            return
        path = info.manifest.source_path
        key = _PIN_KEYS.get(_infer_file_kind(path) or "")
        values = getattr(self.cfg, key) if key else []
        if key is None or path not in values:
            self._on_status(f"{label} is not pinned by an explicit entry.")
            return
        self.apply_config(
            replace(self.cfg, **{key: [v for v in values if v != path]})
        )

    def retire_source(self, label: str) -> None:
        if any(is_exact_exclude(e, label) for e in self.cfg.exclude):
            return
        self.apply_config(
            replace(self.cfg, exclude=[*self.cfg.exclude, exact_exclude_pattern(label)])
        )

    def reinstate_source(self, label: str) -> None:
        self.apply_config(
            replace(
                self.cfg,
                exclude=[e for e in self.cfg.exclude if not is_exact_exclude(e, label)],
            )
        )

    def apply_source_selection(self) -> None:
        """Translate the checkboxes into exact-label excludes and reload."""
        exclude = list(self.cfg.exclude)
        for item in self._iter_source_items():
            if not item.flags() & Qt.ItemFlag.ItemIsEnabled:
                continue
            label, source_state, _detail = item.data(0, Qt.ItemDataRole.UserRole)
            checked = item.checkState(0) == Qt.CheckState.Checked
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
            QMessageBox.information(self, "Mod Sweep", text)

    def _cache_path(self) -> Path:
        return self.cfg.cache or DEFAULT_CACHE

    def _start(
        self, fn, doing: str, then_report: bool = False, then_refresh: bool = False
    ) -> None:
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
        self._worker.finished.connect(
            lambda: self._on_finished(then_report, then_refresh)
        )
        self._worker.start()

    def _on_finished(self, then_report: bool, then_refresh: bool = False) -> None:
        self._set_busy(False, "")
        if then_refresh:
            self.refresh_sources()
        elif then_report:
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
    app.setApplicationName("modsweep")  # machine id: QSettings/paths key on this
    app.setApplicationDisplayName("Mod Sweep")
    app.setWindowIcon(_app_icon())
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    try:
        window = MainWindow(config_path)
    except ValueError as exc:  # e.g. a config path that does not exist
        print(f"error: {exc}", file=sys.stderr)
        return 2
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
