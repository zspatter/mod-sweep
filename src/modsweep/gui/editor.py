"""The config editor dialog and its path-list widgets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config
from .texts import RESOLUTION_HELP


class PathListEditor(QWidget):
    """A list of paths (or plain strings) with add/remove buttons."""

    def __init__(self, values: list, file_filter: str | None, allow_dirs: bool,
                 text_only: bool = False, keyword: str | None = None, parent=None):
        super().__init__(parent)
        self._file_filter = file_filter
        self._keyword = keyword
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        for value in values:
            self.list.addItem(str(value))

        buttons = QHBoxLayout()
        if keyword is not None:
            add_keyword = QPushButton(f"Add '{keyword}'")
            add_keyword.setToolTip(
                "The manifests shipped with the app, plus downloaded updates"
            )
            add_keyword.clicked.connect(self._add_keyword)
            buttons.addWidget(add_keyword)
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

    def _add_keyword(self) -> None:
        if self._keyword is not None and self._keyword not in self.values():
            self.list.addItem(self._keyword)

    def _add_file(self) -> None:  # pragma: no cover - native dialog
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Add file", "", self._file_filter or ""
        )
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
         "Nolvus installer manifests (InstallPackage.xml / .xml.gz). The "
         "'bundled' entry means the manifests shipped with the app plus any "
         "updates fetched by Tools > Update Nolvus Manifests. Adding a "
         "specific file pins that guide version."),
        ("installs", "Installs", None, True,
         "MO2 installations checked for [NoDelete]-prefixed custom "
         "additions: each such mod's source archive is protected in the "
         "downloads folder. Add one install (the folder containing mods/) "
         "or a parent folder - parents are searched a few levels deep, so "
         "nested layouts like Nolvus (Instances\\&lt;name&gt;\\MODS) are "
         "found. Installs without [NoDelete] mods contribute nothing and "
         "are skipped."),
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
        self.setWindowTitle("Edit Mod Sweep config")
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
            editor = PathListEditor(
                getattr(cfg, key), file_filter, allow_dirs,
                keyword="bundled" if key == "nolvus" else None,
            )
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
