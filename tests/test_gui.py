"""GUI smoke tests. Skipped entirely when the gui extra is not installed
(CI runs without PySide6); run headless via the offscreen platform."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from helpers import make_wabbajack, wj_hash  # noqa: E402
from modsweep import gui as gui_mod  # noqa: E402
from modsweep import sweep as sweep_mod  # noqa: E402
from modsweep.gui import MainWindow  # noqa: E402


def app():
    return QApplication.instance() or QApplication([])


def window(cfg):
    win = MainWindow(cfg, show_welcome=False)
    win.show_result_popups = False  # modal dialogs would hang offscreen tests
    return win


def wait_idle(win, timeout_ms=15_000):
    """Wait for the current worker and deliver its queued signals."""
    assert win._worker is not None
    assert win._worker.wait(timeout_ms)
    for _ in range(5):
        QApplication.processEvents()


def build_config(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "claimed.7z").write_bytes(b"CLAIMED")
    (dl / "junk.7z").write_bytes(b"JUNK")
    make_wabbajack(
        tmp_path / "a.wabbajack", "A", "1.0",
        [("claimed.7z", 7, wj_hash(b"CLAIMED"))],
    )
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"""
downloads = '{dl}'
cache = '{tmp_path / "cache.sqlite"}'
wabbajack = ['{tmp_path / "a.wabbajack"}']

[quarantine]
dir = '{tmp_path / "quarantine"}'
""",
        encoding="utf-8",
    )
    return cfg


def swept_window(tmp_path):
    """Window whose junk.7z is already hashed and quarantined."""
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_hash_candidates()  # chains an automatic report
    wait_idle(win)
    wait_idle(win)
    win.run_sweep(apply=False)  # dry run first, like a careful user
    wait_idle(win)
    return win


def test_window_lists_sources_via_worker(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)  # refresh runs threaded
    assert win.sources_list.count() == 1
    assert "A 1.0" in win.sources_list.item(0).text()
    assert "1 active source(s) loaded." in win.console.toPlainText()


def test_report_action_populates_tables(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    assert win.status_table.rowCount() == 5
    assert win.claims_table.rowCount() == 1
    assert win.claims_table.item(0, 0).text() == "A 1.0"  # source column first
    assert win.candidates_table.rowCount() == 1  # junk.7z
    assert "junk.7z" in win.candidates_table.item(0, 2).text()
    assert "reclaim" in win.summary_label.text()


def test_sweep_dry_run_reports_refusal(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_sweep(apply=False)
    wait_idle(win)
    text = win.console.toPlainText()
    assert "Sweep plan:" in text
    assert "Refused (hash never checked): 1" in text  # junk.7z is unhashed
    assert "refused (unhashed)" in text  # status summary line


def test_hash_then_sweep_then_restore_via_gui(tmp_path):
    app()
    win = swept_window(tmp_path)
    dl = tmp_path / "downloads"

    # sweep for real: patch the confirmation dialog to answer Yes
    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win.run_sweep(apply=True)
        wait_idle(win)
        wait_idle(win)  # chained auto-report
    finally:
        QMessageBox.question = original
    assert not (dl / "junk.7z").exists()

    (batch,) = sweep_mod.list_batches(tmp_path / "quarantine")
    win.run_restore(batch.path)
    wait_idle(win)
    wait_idle(win)  # chained auto-report
    assert (dl / "junk.7z").exists()
    assert "Restored 1 files" in win.console.toPlainText()


def test_purge_requires_confirmation_and_deletes(tmp_path, monkeypatch):
    app()
    win = swept_window(tmp_path)
    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win.run_sweep(apply=True)
        wait_idle(win)
        wait_idle(win)
    finally:
        QMessageBox.question = original
    (batch,) = sweep_mod.list_batches(tmp_path / "quarantine")

    # Declining the warning leaves the batch alone.
    monkeypatch.setattr(
        gui_mod.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    win.run_purge(batch.path)
    assert batch.path.exists()

    # Accepting it purges permanently.
    monkeypatch.setattr(
        gui_mod.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.run_purge(batch.path)
    wait_idle(win)
    assert not batch.path.exists()
    assert "permanently deleted" in win.console.toPlainText()


def test_candidate_status_has_hover_help(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    tooltip = win.candidates_table.item(0, 1).toolTip()
    assert "No active source references this file" in tooltip


def test_quarantine_single_file_moves_it_and_its_meta_only(tmp_path):
    app()
    dl = tmp_path / "downloads"
    win = window(build_config(tmp_path))
    (dl / "junk.7z.meta").write_text("[General]\n", encoding="utf-8")
    wait_idle(win)
    win.run_hash_candidates()
    wait_idle(win)
    wait_idle(win)  # chained report stores _last_results

    win.quarantine_file("junk.7z")
    wait_idle(win)
    wait_idle(win)  # chained report
    assert not (dl / "junk.7z").exists()
    assert not (dl / "junk.7z.meta").exists()
    assert (dl / "claimed.7z").exists()
    (batch,) = sweep_mod.list_batches(tmp_path / "quarantine")
    assert batch.path.name.endswith("_file")
    assert "Quarantined junk.7z" in win.console.toPlainText()


def test_delete_single_file_confirms_then_purges(tmp_path, monkeypatch):
    app()
    dl = tmp_path / "downloads"
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_hash_candidates()
    wait_idle(win)
    wait_idle(win)

    monkeypatch.setattr(
        gui_mod.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    win.delete_file("junk.7z")
    assert (dl / "junk.7z").exists()  # declined: nothing happened

    monkeypatch.setattr(
        gui_mod.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.delete_file("junk.7z")
    wait_idle(win)
    wait_idle(win)
    assert not (dl / "junk.7z").exists()
    assert sweep_mod.list_batches(tmp_path / "quarantine") == []  # purged
    assert "Deleted junk.7z" in win.console.toPlainText()


def test_pipeline_log_lines_drain_into_console(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    text = win.console.toPlainText()
    assert "INFO modsweep.matcher: matched" in text


def build_two_list_config(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    make_wabbajack(tmp_path / "b.wabbajack", "B", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'a.wabbajack'}', '{tmp_path / 'b.wabbajack'}']\n",
        encoding="utf-8",
    )
    return cfg


def find_item(win, label):
    from PySide6.QtCore import Qt

    for i in range(win.sources_list.count()):
        item = win.sources_list.item(i)
        if item.data(Qt.ItemDataRole.UserRole)[0] == label:
            return item
    raise AssertionError(f"{label} not in sources list")


def test_untick_and_apply_retires_then_reinstates(tmp_path):
    from PySide6.QtCore import Qt

    from modsweep import config

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    assert not win.apply_selection_btn.isEnabled()

    find_item(win, "B 1.0").setCheckState(Qt.CheckState.Unchecked)
    assert win.apply_selection_btn.isEnabled()
    win.apply_source_selection()
    wait_idle(win)  # save + refresh
    assert config.load(tmp_path / "modsweep.toml").exclude == ["B 1.0"]
    item = find_item(win, "B 1.0")
    assert item.checkState() == Qt.CheckState.Unchecked
    assert item.flags() & Qt.ItemFlag.ItemIsEnabled  # exact exclude: re-tickable

    item.setCheckState(Qt.CheckState.Checked)
    win.apply_source_selection()
    wait_idle(win)
    assert config.load(tmp_path / "modsweep.toml").exclude == []
    assert find_item(win, "B 1.0").checkState() == Qt.CheckState.Checked


def test_glob_excluded_source_is_locked(tmp_path):
    from PySide6.QtCore import Qt

    cfg_path = build_two_list_config(tmp_path)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "exclude = ['B*']\n", encoding="utf-8"
    )
    app()
    win = window(cfg_path)
    wait_idle(win)
    item = find_item(win, "B 1.0")
    assert item.checkState() == Qt.CheckState.Unchecked
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "B*" in item.toolTip()


def test_select_none_then_all_roundtrip(tmp_path):
    from PySide6.QtCore import Qt

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    win._set_all_sources(False)
    assert all(
        win.sources_list.item(i).checkState() == Qt.CheckState.Unchecked
        for i in range(win.sources_list.count())
    )
    win._set_all_sources(True)
    assert all(
        win.sources_list.item(i).checkState() == Qt.CheckState.Checked
        for i in range(win.sources_list.count())
    )


def test_latest_only_locks_superseded_with_visible_hint(tmp_path):
    from PySide6.QtCore import Qt

    dl = tmp_path / "downloads"
    dl.mkdir()
    make_wabbajack(tmp_path / "old.wabbajack", "X", "1.0", [])
    make_wabbajack(tmp_path / "new.wabbajack", "X", "2.0", [])
    cfg = tmp_path / "modsweep.toml"
    # Point at the folder: explicit files would be pinned and never locked.
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"latest_only = true\nwabbajack = ['{tmp_path}']\n",
        encoding="utf-8",
    )
    app()
    win = window(cfg)
    wait_idle(win)
    # Superseded versions hide by default: a growing manifest bundle must
    # not drown the list. The toggle carries the hidden count.
    assert win.show_superseded.text() == "Show superseded (1)"
    with pytest.raises(AssertionError):
        find_item(win, "X 1.0")
    win.show_superseded.setChecked(True)
    item = find_item(win, "X 1.0")
    assert "locked by latest_only" in item.text()  # visible without hovering
    assert item.font().italic()
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert not item.icon().isNull()  # lock icon
    assert "pin" in item.toolTip().lower()  # the hover hint explains the way out
    assert find_item(win, "X 2.0").checkState() == Qt.CheckState.Checked


def test_snapshot_from_gui(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    out = tmp_path / "snaps"
    win.run_snapshot(out)
    wait_idle(win)
    assert len(list(out.glob("*.json"))) == 1
    assert "1 snapshot(s) written" in win.console.toPlainText()


def test_purge_confirmation_flags_young_batches(tmp_path, monkeypatch):
    app()
    win = swept_window(tmp_path)
    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win.run_sweep(apply=True)
        wait_idle(win)
        wait_idle(win)
    finally:
        QMessageBox.question = original
    (batch,) = sweep_mod.list_batches(tmp_path / "quarantine")

    prompts = []
    monkeypatch.setattr(
        gui_mod.QMessageBox, "warning",
        staticmethod(
            lambda parent, title, text, *a, **k: (
                prompts.append(text), QMessageBox.StandardButton.No
            )[1]
        ),
    )
    win.run_purge(batch.path)
    assert batch.path.exists()  # declined
    assert "younger than the 30-day trust period" in prompts[0]
    assert "purges whatever you pick" in prompts[0]


def latest_only_folder_config(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    make_wabbajack(tmp_path / "old.wabbajack", "X", "1.0", [])
    make_wabbajack(tmp_path / "new.wabbajack", "X", "2.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"latest_only = true\nwabbajack = ['{tmp_path}']\n",
        encoding="utf-8",
    )
    return cfg


def test_pin_source_adds_explicit_entry_and_unlocks(tmp_path):
    from PySide6.QtCore import Qt

    from modsweep import config

    app()
    win = window(latest_only_folder_config(tmp_path))
    wait_idle(win)
    win.show_superseded.setChecked(True)
    assert "locked by latest_only" in find_item(win, "X 1.0").text()

    win.pin_source("X 1.0")  # works even while the item is hidden
    wait_idle(win)  # save + refresh
    assert tmp_path / "old.wabbajack" in config.load(tmp_path / "modsweep.toml").wabbajack
    item = find_item(win, "X 1.0")
    assert item.checkState() == Qt.CheckState.Checked
    assert not item.icon().isNull()  # pin icon: visible without hovering
    assert "Pinned" in item.toolTip()

    win.pin_source("X 1.0")  # idempotent
    assert "already pinned" in win.console.toPlainText()

    win.unpin_source("X 1.0")  # back to superseded (and hidden by default)
    wait_idle(win)
    assert tmp_path / "old.wabbajack" not in config.load(tmp_path / "modsweep.toml").wabbajack
    win.show_superseded.setChecked(True)
    assert "locked by latest_only" in find_item(win, "X 1.0").text()


def test_retire_and_reinstate_via_context_actions(tmp_path):
    from PySide6.QtCore import Qt

    from modsweep import config

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    win.retire_source("B 1.0")
    wait_idle(win)
    assert config.load(tmp_path / "modsweep.toml").exclude == ["B 1.0"]
    retired = find_item(win, "B 1.0")
    assert retired.checkState() == Qt.CheckState.Unchecked
    assert not retired.icon().isNull()  # ban icon marks the exclusion

    win.reinstate_source("B 1.0")
    wait_idle(win)
    assert config.load(tmp_path / "modsweep.toml").exclude == []
    assert find_item(win, "B 1.0").checkState() == Qt.CheckState.Checked


def test_pin_active_source_shows_pinned_state(tmp_path):
    from PySide6.QtCore import Qt

    from modsweep import config

    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    lists = tmp_path / "lists"
    lists.mkdir()
    make_wabbajack(lists / "a.wabbajack", "A", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{lists}']\n",  # folder: implicit, so A starts active
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    assert "untick to retire" in find_item(win, "A 1.0").toolTip()

    win.pin_source("A 1.0")  # pin for future use, ahead of any filter
    wait_idle(win)
    assert lists / "a.wabbajack" in config.load(cfg).wabbajack
    item = find_item(win, "A 1.0")
    assert item.checkState() == Qt.CheckState.Checked
    assert not item.icon().isNull()  # pin icon marks it without hovering
    assert "never dropped by latest_only" in item.toolTip()

    win.unpin_source("A 1.0")  # roundtrip: explicit entry removed
    wait_idle(win)
    assert lists / "a.wabbajack" not in config.load(cfg).wabbajack
    item = find_item(win, "A 1.0")
    assert item.icon().isNull()  # plain active again
    assert "untick to retire" in item.toolTip()

    win.unpin_source("A 1.0")  # not pinned anymore: no-op with a message
    assert "not pinned by an explicit entry" in win.console.toPlainText()


def test_pin_source_rejects_unpinnable_kinds(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.pin_source("nonsense")
    assert "not in the current source list" in win.console.toPlainText()


def test_action_results_pop_up(tmp_path, monkeypatch):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.show_result_popups = True
    seen = []
    monkeypatch.setattr(
        gui_mod.QMessageBox, "information",
        staticmethod(lambda parent, title, text, *a, **k: seen.append(text)),
    )
    win.run_sweep(apply=False)
    wait_idle(win)
    assert any("Dry run:" in text for text in seen)


def test_config_editor_prefills_and_builds_result(tmp_path):
    app()
    from modsweep import config
    from modsweep.gui import ConfigEditorDialog

    cfg = config.Config(
        downloads=tmp_path / "dl",
        cache=tmp_path / "cache.sqlite",
        wabbajack=[tmp_path / "Wabbajack"],
        exclude=["NGVO*"],
        latest_only=True,
        quarantine=tmp_path / "q",
        quarantine_keep_days=14,
    )
    dialog = ConfigEditorDialog(cfg)
    assert dialog.downloads_edit.text() == str(tmp_path / "dl")
    assert dialog.editors["wabbajack"].values() == [str(tmp_path / "Wabbajack")]
    assert dialog.exclude_editor.values() == ["NGVO*"]
    assert dialog.latest_only.isChecked()
    assert dialog.keep_days.value() == 14

    dialog.downloads_edit.setText(str(tmp_path / "other"))
    dialog.editors["installs"].list.addItem(str(tmp_path / "MO2"))
    dialog.exclude_editor.pattern_edit.setText("Apostasy*")
    dialog.exclude_editor._add_text()
    dialog.latest_only.setChecked(False)

    result = dialog.result_config()
    assert result.downloads == tmp_path / "other"
    assert result.cache == tmp_path / "cache.sqlite"  # preserved though unedited
    assert result.installs == [tmp_path / "MO2"]
    assert result.exclude == ["NGVO*", "Apostasy*"]
    assert result.latest_only is False


def test_apply_config_saves_and_refreshes(tmp_path):
    from PySide6.QtCore import Qt

    app()
    from modsweep import config

    win = window(build_config(tmp_path))
    wait_idle(win)
    edited = config.load(tmp_path / "modsweep.toml")
    edited.exclude = ["A*"]  # retire the only list
    win.apply_config(edited)
    wait_idle(win)
    # Excluded sources stay visible (unchecked) so they can be reinstated;
    # a glob exclude locks the checkbox and points at the editor.
    item = find_item(win, "A 1.0")
    assert item.checkState() == Qt.CheckState.Unchecked
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "0 active source(s) loaded." in win.console.toPlainText()
    assert config.load(tmp_path / "modsweep.toml").exclude == ["A*"]


def test_tables_sort_numerically(tmp_path):
    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "big.7z").write_bytes(b"B" * 3000)
    (dl / "small.7z").write_bytes(b"s" * 10)
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'a.wabbajack'}']\n",
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    table = win.candidates_table
    assert table.rowCount() == 2
    table.sortItems(0)  # ascending by size: numeric, not lexicographic
    assert "small.7z" in table.item(0, 2).text()
    assert "big.7z" in table.item(1, 2).text()
