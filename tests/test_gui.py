"""GUI smoke tests. Skipped entirely when the gui extra is not installed
(CI runs without PySide6); run headless via the offscreen platform."""

import os
import typing

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import make_wabbajack, wj_hash
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from modsweep import gui as gui_mod
from modsweep import sweep as sweep_mod
from modsweep.gui import MainWindow


@pytest.fixture(autouse=True)
def _reset_pipeline_logger():
    """Each MainWindow raises the shared 'modsweep' logger to INFO and adds
    a handler; restore both so test outcomes don't depend on file order."""
    import logging

    logger = logging.getLogger("modsweep")
    level, handlers = logger.level, list(logger.handlers)
    yield
    logger.setLevel(level)
    logger.handlers = handlers


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
    win.run_hash_candidates()  # hashes and reports in one pass
    wait_idle(win)
    win.run_sweep(apply=False)  # dry run first, like a careful user
    wait_idle(win)
    return win


def test_window_lists_sources_via_worker(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)  # refresh runs threaded
    assert win.sources_list.topLevelItemCount() == 1
    assert "A 1.0" in win.sources_list.topLevelItem(0).text(0)
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
    # Restore cleans up after itself: no husk batch remains selectable.
    assert sweep_mod.list_batches(tmp_path / "quarantine") == []
    assert not batch.path.exists()


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
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    win.run_purge(batch.path)
    assert batch.path.exists()

    # Accepting it purges permanently.
    monkeypatch.setattr(
        QMessageBox, "warning",
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
    wait_idle(win)  # the same pass reports, storing _last_results

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

    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    win.delete_file("junk.7z")
    assert (dl / "junk.7z").exists()  # declined: nothing happened

    monkeypatch.setattr(
        QMessageBox, "warning",
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


def all_source_items(win):
    for i in range(win.sources_list.topLevelItemCount()):
        top = win.sources_list.topLevelItem(i)
        yield top
        for j in range(top.childCount()):
            yield top.child(j)


def find_item(win, label):
    from PySide6.QtCore import Qt

    for item in all_source_items(win):
        if item.data(0, Qt.ItemDataRole.UserRole)[0] == label:
            return item
    raise AssertionError(f"{label} not in sources list")


def test_untick_and_apply_retires_then_reinstates(tmp_path):
    from PySide6.QtCore import Qt

    from modsweep import config

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    assert not win.apply_selection_btn.isEnabled()

    find_item(win, "B 1.0").setCheckState(0, Qt.CheckState.Unchecked)
    assert win.apply_selection_btn.isEnabled()
    win.apply_source_selection()
    wait_idle(win)  # save + refresh
    assert config.load(tmp_path / "modsweep.toml").exclude == ["B 1.0"]
    item = find_item(win, "B 1.0")
    assert item.checkState(0) == Qt.CheckState.Unchecked
    assert item.flags() & Qt.ItemFlag.ItemIsEnabled  # exact exclude: re-tickable

    item.setCheckState(0, Qt.CheckState.Checked)
    win.apply_source_selection()
    wait_idle(win)
    assert config.load(tmp_path / "modsweep.toml").exclude == []
    assert find_item(win, "B 1.0").checkState(0) == Qt.CheckState.Checked


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
    assert item.checkState(0) == Qt.CheckState.Unchecked
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "B*" in item.toolTip(0)


def test_select_none_then_all_roundtrip(tmp_path):
    from PySide6.QtCore import Qt

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    win._set_all_sources(False)
    assert all(
        item.checkState(0) == Qt.CheckState.Unchecked
        for item in all_source_items(win)
    )
    win._set_all_sources(True)
    assert all(
        item.checkState(0) == Qt.CheckState.Checked
        for item in all_source_items(win)
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
    # Versions group under their newest: routine list updates nest as
    # collapsed children instead of flooding the list.
    parent = find_item(win, "X 2.0")
    assert "[+1 older]" in parent.text(0)
    assert parent.checkState(0) == Qt.CheckState.Checked
    assert not parent.isExpanded()  # all children superseded: stay folded
    item = find_item(win, "X 1.0")
    assert item.parent() is parent
    assert "locked by latest_only" in item.text(0)  # visible when expanded
    assert item.font(0).italic()
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert not item.icon(0).isNull()  # lock icon
    assert "pin" in item.toolTip(0).lower()  # the hover hint explains the way out


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
        QMessageBox, "warning",
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
    assert "locked by latest_only" in find_item(win, "X 1.0").text(0)

    win.pin_source("X 1.0")  # works even while the group is collapsed
    wait_idle(win)  # save + refresh
    assert tmp_path / "old.wabbajack" in config.load(tmp_path / "modsweep.toml").wabbajack
    item = find_item(win, "X 1.0")
    assert item.checkState(0) == Qt.CheckState.Checked
    assert not item.icon(0).isNull()  # pin icon: visible without hovering
    assert "Pinned" in item.toolTip(0)
    assert item.parent().isExpanded()  # a pinned child auto-expands its group

    win.pin_source("X 1.0")  # idempotent
    assert "already pinned" in win.console.toPlainText()

    win.unpin_source("X 1.0")  # back to superseded (and folded away)
    wait_idle(win)
    assert tmp_path / "old.wabbajack" not in config.load(tmp_path / "modsweep.toml").wabbajack
    item = find_item(win, "X 1.0")
    assert "locked by latest_only" in item.text(0)
    assert not item.parent().isExpanded()


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
    assert retired.checkState(0) == Qt.CheckState.Unchecked
    assert not retired.icon(0).isNull()  # ban icon marks the exclusion

    win.reinstate_source("B 1.0")
    wait_idle(win)
    assert config.load(tmp_path / "modsweep.toml").exclude == []
    assert find_item(win, "B 1.0").checkState(0) == Qt.CheckState.Checked


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
    assert "untick to retire" in find_item(win, "A 1.0").toolTip(0)

    win.pin_source("A 1.0")  # pin for future use, ahead of any filter
    wait_idle(win)
    assert lists / "a.wabbajack" in config.load(cfg).wabbajack
    item = find_item(win, "A 1.0")
    assert item.checkState(0) == Qt.CheckState.Checked
    assert not item.icon(0).isNull()  # pin icon marks it without hovering
    assert "never dropped by latest_only" in item.toolTip(0)

    win.unpin_source("A 1.0")  # roundtrip: explicit entry removed
    wait_idle(win)
    assert lists / "a.wabbajack" not in config.load(cfg).wabbajack
    item = find_item(win, "A 1.0")
    assert item.icon(0).isNull()  # plain active again
    assert "untick to retire" in item.toolTip(0)

    win.unpin_source("A 1.0")  # not pinned anymore: no-op with a message
    assert "not pinned by an explicit entry" in win.console.toPlainText()


def test_pin_source_rejects_unpinnable_kinds(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.pin_source("nonsense")
    assert "not in the current source list" in win.console.toPlainText()


class FakeSettings:
    """Stands in for QSettings so welcome-suppression is testable without
    touching the real registry/plist."""

    store: typing.ClassVar[dict] = {}

    def __init__(self, *args):
        pass

    def value(self, key, default=False, type=bool):
        return FakeSettings.store.get(key, default)

    def setValue(self, key, value):
        FakeSettings.store[key] = value


def test_welcome_popup_shows_once_when_suppressed(tmp_path, monkeypatch):
    app()
    FakeSettings.store = {}
    monkeypatch.setattr(gui_mod.window, "QSettings", FakeSettings)
    shown = []

    def fake_exec(box):
        shown.append(box.windowTitle())
        box.checkBox().setChecked(True)  # the user ticks "don't show again"
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)

    cfg = build_config(tmp_path)
    first = MainWindow(cfg, show_welcome=True)
    first.show_result_popups = False
    wait_idle(first)
    assert shown == ["Welcome to Mod Sweep"]
    assert FakeSettings.store["welcome/suppressed"] is True

    second = MainWindow(cfg, show_welcome=True)  # suppressed: no dialog
    second.show_result_popups = False
    wait_idle(second)
    assert shown == ["Welcome to Mod Sweep"]


def test_welcome_popup_repeats_until_suppressed(tmp_path, monkeypatch):
    app()
    FakeSettings.store = {}
    monkeypatch.setattr(gui_mod.window, "QSettings", FakeSettings)
    shown = []
    monkeypatch.setattr(
        QMessageBox, "exec",
        lambda box: (shown.append(box.windowTitle()), 0)[1],  # checkbox left alone
    )
    cfg = build_config(tmp_path)
    for _ in range(2):
        win = MainWindow(cfg, show_welcome=True)
        win.show_result_popups = False
        wait_idle(win)
    assert len(shown) == 2  # keeps showing until the user opts out
    assert "welcome/suppressed" not in FakeSettings.store


def test_busy_state_disables_actions_and_shows_progress(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win._set_busy(True, "Working")
    assert not win.buttons["report"].isEnabled()
    assert not win.buttons["apply"].isEnabled()
    assert win.buttons["open"].isEnabled()  # config stays reachable
    assert win.progress.isVisibleTo(win)
    assert win.progress.maximum() == 0  # indeterminate until real progress
    win._set_busy(False, "")
    assert win.buttons["report"].isEnabled()
    assert not win.progress.isVisibleTo(win)


def test_worker_failure_logs_error_and_reenables(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)

    def action(worker):
        raise RuntimeError("downloads drive unplugged")

    win._start(action, "Exploding")
    wait_idle(win)
    assert "error: downloads drive unplugged" in win.console.toPlainText()
    assert win.buttons["report"].isEnabled()  # busy state cleared after failure


def test_select_all_none_skip_locked_items(tmp_path):
    from PySide6.QtCore import Qt

    cfg_path = build_two_list_config(tmp_path)
    cfg_path.write_text(
        cfg_path.read_text(encoding="utf-8") + "exclude = ['B*']\n", encoding="utf-8"
    )
    app()
    win = window(cfg_path)
    wait_idle(win)
    win._set_all_sources(True)
    locked = find_item(win, "B 1.0")
    assert locked.checkState(0) == Qt.CheckState.Unchecked  # glob lock holds
    assert find_item(win, "A 1.0").checkState(0) == Qt.CheckState.Checked


def test_apply_selection_resets_dirty_state(tmp_path):
    from PySide6.QtCore import Qt

    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)
    find_item(win, "B 1.0").setCheckState(0, Qt.CheckState.Unchecked)
    assert win.apply_selection_btn.isEnabled()
    win.apply_source_selection()
    wait_idle(win)
    assert not win.apply_selection_btn.isEnabled()  # clean after refresh


def test_refresh_warns_when_source_vanishes(tmp_path):
    app()
    win = window(build_two_list_config(tmp_path))
    wait_idle(win)  # baseline recorded

    (tmp_path / "b.wabbajack").unlink()
    win.refresh_sources()
    wait_idle(win)
    assert "previously-active source vanished: B 1.0" in win.console.toPlainText()

    win.refresh_sources()  # baseline accepted: no repeat
    wait_idle(win)
    assert win.console.toPlainText().count("vanished: B 1.0") == 1


def test_sweep_apply_updates_report_tab(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_hash_candidates()
    wait_idle(win)  # the same pass fills the report tables
    assert win.candidates_table.rowCount() == 1
    win.tabs.setCurrentIndex(1)  # user wanders off to the Log

    original = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win.run_sweep(apply=True)
        wait_idle(win)
        wait_idle(win)  # chained report
    finally:
        QMessageBox.question = original
    assert win.tabs.currentIndex() == 0  # pulled back to the fresh report
    assert win.candidates_table.rowCount() == 0  # swept clean


def test_config_editor_keyword_button_is_idempotent(tmp_path):
    app()
    from modsweep import config
    from modsweep.gui import ConfigEditorDialog

    dialog = ConfigEditorDialog(config.Config())
    editor = dialog.editors["nolvus"]
    editor._add_keyword()
    editor._add_keyword()
    assert editor.values() == ["bundled"]


def test_tree_groups_renamed_lists_by_machine_id(tmp_path):
    import json as json_mod

    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    lists = tmp_path / "lists"
    lists.mkdir()
    make_wabbajack(lists / "ls3.wabbajack", "Living Skyrim", "3.0", [])
    make_wabbajack(lists / "ls4.wabbajack", "Living Skyrim 4", "4.0", [])
    for name in ("ls3", "ls4"):
        (lists / f"{name}.wabbajack.metadata").write_text(
            json_mod.dumps({"links": {"machineURL": "living_skyrim"}}),
            encoding="utf-8",
        )
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{lists}']\n",
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    # Renamed between releases, same machine id: one tree group, newest on top.
    assert win.sources_list.topLevelItemCount() == 1
    parent = win.sources_list.topLevelItem(0)
    assert parent.text(0).startswith("Living Skyrim 4 4.0")
    assert "[+1 older]" in parent.text(0)
    assert parent.child(0).text(0).startswith("Living Skyrim 3.0")


def test_sources_group_alphabetically_newest_on_top(tmp_path):
    from PySide6.QtCore import Qt

    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    lists = tmp_path / "lists"
    lists.mkdir()
    make_wabbajack(lists / "b.wabbajack", "Bravo", "1.0", [])
    make_wabbajack(lists / "a1.wabbajack", "alpha", "1.0", [])  # case-insensitive
    make_wabbajack(lists / "a2.wabbajack", "alpha", "2.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{lists}']\n",  # no latest_only: all versions active
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)

    assert win.sources_list.topLevelItemCount() == 2  # alpha group, Bravo
    alpha, bravo = (win.sources_list.topLevelItem(i) for i in range(2))
    assert alpha.text(0).startswith("alpha 2.0")  # newest version is the parent
    assert "[+1 older]" in alpha.text(0)
    assert alpha.isExpanded()  # child is active, so it must stay visible
    assert alpha.child(0).text(0).startswith("alpha 1.0")
    assert alpha.child(0).checkState(0) == Qt.CheckState.Checked
    assert bravo.text(0).startswith("Bravo 1.0")
    assert bravo.childCount() == 0  # single-version lists stay flat


def test_action_results_pop_up(tmp_path, monkeypatch):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.show_result_popups = True
    seen = []
    monkeypatch.setattr(
        QMessageBox, "information",
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
    assert item.checkState(0) == Qt.CheckState.Unchecked
    assert not item.flags() & Qt.ItemFlag.ItemIsEnabled
    assert "0 active source(s) loaded." in win.console.toPlainText()
    assert config.load(tmp_path / "modsweep.toml").exclude == ["A*"]


def test_manifest_update_action_refreshes_sources(tmp_path, monkeypatch):
    from modsweep import remote

    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    monkeypatch.setattr(remote, "update_manifests", lambda: ["nolvus-7.0.xml.gz"])
    win.run_manifest_update()
    wait_idle(win)
    wait_idle(win)  # chained source refresh
    assert "Downloaded 1 new manifest(s): nolvus-7.0.xml.gz" in win.console.toPlainText()


def test_update_check_offers_release_page(tmp_path, monkeypatch):
    from modsweep import remote

    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    monkeypatch.setattr(
        remote, "check_update",
        lambda current: remote.UpdateInfo(current, "9.9.9", "https://example/rel"),
    )
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(
            lambda parent, title, text, *a, **k: (
                prompts.append(text), QMessageBox.StandardButton.No
            )[1]
        ),
    )
    win.run_update_check()
    wait_idle(win)
    assert any("v9.9.9 is available" in text for text in prompts)

    monkeypatch.setattr(remote, "check_update", lambda current: None)
    win.run_update_check()
    wait_idle(win)
    assert "is up to date" in win.console.toPlainText()


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


def test_systemexit_in_worker_surfaces_instead_of_killing_process(tmp_path):
    """SystemExit escaping a QThread would take down the whole GUI; the
    worker must catch it and report like any other failure."""
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)

    def action(worker):
        raise SystemExit("quarantine dir must not be inside the downloads dir")

    win._start(action, "Exploding")
    wait_idle(win)
    assert "quarantine dir must not be inside" in win.console.toPlainText()
    assert win.buttons["report"].isEnabled()


def test_quarantine_inside_downloads_reports_error_in_gui(tmp_path):
    cfg_path = build_config(tmp_path)
    text = cfg_path.read_text(encoding="utf-8")
    dl = tmp_path / "downloads"
    text = text.replace(
        f"dir = '{tmp_path / 'quarantine'}'", f"dir = '{dl / 'q'}'"
    )
    cfg_path.write_text(text, encoding="utf-8")
    app()
    win = window(cfg_path)
    wait_idle(win)
    win.run_sweep(apply=False)
    wait_idle(win)
    assert "error: quarantine dir must not be inside" in win.console.toPlainText()
    assert win.buttons["report"].isEnabled()  # window alive, busy state cleared


def test_actions_refuse_without_downloads_dir(tmp_path):
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"cache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'a.wabbajack'}']\n",
        encoding="utf-8",
    )
    app()
    win = window(cfg)
    wait_idle(win)
    before = win._worker
    win.run_report()
    assert win._worker is before  # no worker started
    assert "no downloads directory configured" in win.console.toPlainText()


def test_sweep_apply_declined_confirmation_does_nothing(tmp_path, monkeypatch):
    app()
    win = swept_window(tmp_path)
    before = win._worker
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No),
    )
    win.run_sweep(apply=True)
    assert win._worker is before  # declined: no worker started
    assert (tmp_path / "downloads" / "junk.7z").exists()


def test_pick_batch_reports_empty_quarantine(tmp_path, monkeypatch):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    seen = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda parent, title, text, *a, **k: seen.append(text)),
    )
    win.run_restore()  # no batch given: the picker finds nothing to offer
    assert any("quarantine is empty" in s for s in seen)


def test_pick_batch_selection_flows_into_restore(tmp_path, monkeypatch):
    app()
    win = swept_window(tmp_path)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.run_sweep(apply=True)
    wait_idle(win)
    wait_idle(win)  # chained report
    assert not (tmp_path / "downloads" / "junk.7z").exists()

    # The picker defaults to the newest batch; accept it.
    monkeypatch.setattr(
        QInputDialog, "getItem",
        staticmethod(lambda parent, title, label, items, current, editable: (
            items[current], True
        )),
    )
    win.run_restore()
    wait_idle(win)
    assert (tmp_path / "downloads" / "junk.7z").exists()


def test_open_config_switches_active_config(tmp_path, monkeypatch):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = build_config(other_dir)
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(other), "")),
    )
    win.open_config()
    wait_idle(win)  # sources refresh against the new config
    assert win.config_path == other
    assert str(other) in win.status_config.text()


# --- action endpoints: outcomes and refusal paths ---------------------------


def test_sweep_apply_with_no_candidates_reports_nothing_to_move(tmp_path, monkeypatch):
    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "claimed.7z").write_bytes(b"CLAIMED")
    make_wabbajack(
        tmp_path / "a.wabbajack", "A", "1.0",
        [("claimed.7z", 7, wj_hash(b"CLAIMED"))],
    )
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'a.wabbajack'}']\n"
        f"[quarantine]\ndir = '{tmp_path / 'q'}'\n",
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.run_sweep(apply=True)
    wait_idle(win)
    assert "Nothing to move - no eligible candidates." in win.console.toPlainText()
    assert (dl / "claimed.7z").exists()


def test_restore_summary_reports_skipped_and_missing(tmp_path, monkeypatch):
    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "junk1.7z").write_bytes(b"J1")
    (dl / "junk2.7z").write_bytes(b"J2")
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'a.wabbajack'}']\n"
        f"[quarantine]\ndir = '{tmp_path / 'q'}'\n",
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    win.run_hash_candidates()
    wait_idle(win)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.run_sweep(apply=True)
    wait_idle(win)
    wait_idle(win)  # chained report
    (batch,) = sweep_mod.list_batches(tmp_path / "q")

    (dl / "junk1.7z").write_bytes(b"OCCUPANT")  # original path taken
    (batch.path / "junk2.7z").unlink()  # gone from the batch

    win.run_restore(batch.path)
    wait_idle(win)
    text = win.console.toPlainText()
    assert "1 skipped (original path occupied)." in text
    assert "1 missing from the batch." in text


def test_quarantine_file_requires_a_report_first(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.quarantine_file("junk.7z")  # no report has run: nothing stored
    assert "not in the last report - run Report first" in win.console.toPlainText()


def test_quarantine_file_refuses_unhashed_candidate(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_report()  # stores results; junk.7z is a candidate but unhashed
    wait_idle(win)
    win.quarantine_file("junk.7z")
    wait_idle(win)
    text = win.console.toPlainText()
    assert "Refused: junk.7z has no verified hash" in text
    assert (tmp_path / "downloads" / "junk.7z").exists()


def test_quarantine_file_rejects_protected_file(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    win.quarantine_file("claimed.7z")  # keep-status: never a candidate
    wait_idle(win)
    assert "claimed.7z is not an eligible candidate." in win.console.toPlainText()
    assert (tmp_path / "downloads" / "claimed.7z").exists()


def test_manifest_update_reports_up_to_date(tmp_path, monkeypatch):
    from modsweep import remote

    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    monkeypatch.setattr(remote, "update_manifests", lambda: [])
    win.run_manifest_update()
    wait_idle(win)
    wait_idle(win)  # chained source refresh
    assert "Bundled manifests are up to date." in win.console.toPlainText()


# --- batch descriptions and the trust-period note ----------------------------


def test_describe_batch_falls_back_for_unknown_paths(tmp_path):
    from pathlib import Path

    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    ghost = Path(tmp_path / "q" / "not-a-batch")
    assert win._describe_batch(ghost) == str(ghost)
    assert win._trust_period_note(ghost) == ""


def test_trust_period_note_only_for_young_batches(tmp_path, monkeypatch):
    app()
    win = swept_window(tmp_path)
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    win.run_sweep(apply=True)
    wait_idle(win)
    wait_idle(win)
    (batch,) = sweep_mod.list_batches(tmp_path / "quarantine")
    assert "younger than the" in win._trust_period_note(batch.path)

    aged = batch.path.with_name("2020-01-01_000000")
    batch.path.rename(aged)
    assert win._trust_period_note(aged) == ""  # past the trust period: no note


# --- pin/unpin error paths ----------------------------------------------------


def test_pin_and_unpin_reject_unknown_labels(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win.pin_source("Ghost 9.9")
    win.unpin_source("Ghost 9.9")
    text = win.console.toPlainText()
    assert text.count("is not in the current source list") == 2


def test_pin_source_already_pinned_is_a_noop(tmp_path):
    app()
    win = window(build_config(tmp_path))  # wabbajack entry is a file: pinned
    wait_idle(win)
    before = win.cfg
    win.pin_source("A 1.0")
    assert "already pinned." in win.console.toPlainText()
    assert win.cfg is before  # config untouched


def test_unpin_source_requires_an_explicit_entry(tmp_path):
    app()
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "x.7z").write_bytes(b"X")
    (tmp_path / "lists").mkdir()
    make_wabbajack(tmp_path / "lists" / "a.wabbajack", "A", "1.0", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{tmp_path / 'c.sqlite'}'\n"
        f"wabbajack = ['{tmp_path / 'lists'}']\n",  # dir walk: implicit
        encoding="utf-8",
    )
    win = window(cfg)
    wait_idle(win)
    win.unpin_source("A 1.0")
    assert "not pinned by an explicit entry." in win.console.toPlainText()


# --- widget and plumbing units -------------------------------------------------


def test_numeric_item_falls_back_to_text_compare_with_plain_items(tmp_path):
    from PySide6.QtWidgets import QTableWidgetItem

    from modsweep.gui.window import NumericItem

    app()
    assert NumericItem(2, "2").__lt__(QTableWidgetItem("10")) is False  # "2" > "10"
    assert (NumericItem(2, "2") < NumericItem(10, "10")) is True  # numeric


def test_start_ignores_second_action_while_busy(tmp_path):
    import threading

    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    gate = threading.Event()

    def slow(worker):
        gate.wait(10)

    win._start(slow, "Slow")
    first = win._worker
    win._start(lambda worker: None, "Second")  # one action at a time
    assert win._worker is first
    gate.set()
    wait_idle(win)


def test_progress_signal_switches_between_modes(tmp_path):
    app()
    win = window(build_config(tmp_path))
    wait_idle(win)
    win._on_progress(3, 10)
    assert (win.progress.maximum(), win.progress.value()) == (10, 3)
    win._on_progress(0, 0)  # totals of zero mean indeterminate again
    assert win.progress.maximum() == 0


def test_linestream_buffers_partial_lines_and_flushes(tmp_path):
    from modsweep.gui.workers import _LineStream

    seen = []
    stream = _LineStream(seen.append)
    stream.write("first line\nsecond ")
    assert seen == ["first line"]
    stream.write("half\n\n   \n")  # blank lines never reach the log
    assert seen == ["first line", "second half"]
    stream.write("tail without newline")
    stream.flush_pending()
    assert seen[-1] == "tail without newline"
    stream.flush_pending()  # nothing pending: no duplicate
    assert seen.count("tail without newline") == 1


def test_gui_log_handler_formats_through_bridge(tmp_path):
    import logging

    from modsweep.gui.workers import GuiLogHandler, LogBridge

    app()
    seen = []
    bridge = LogBridge()
    bridge.message.connect(seen.append)
    handler = GuiLogHandler(bridge)
    record = logging.LogRecord(
        "modsweep.matcher", logging.INFO, __file__, 1, "matched %d files", (7,), None
    )
    handler.emit(record)
    assert seen == ["INFO modsweep.matcher: matched 7 files"]


def test_path_list_editor_add_and_remove(tmp_path):
    from modsweep.gui.editor import PathListEditor

    app()
    editor = PathListEditor(["one", "two"], None, False, text_only=True)
    editor.pattern_edit.setText("   ")  # whitespace only: nothing added
    editor._add_text()
    assert editor.values() == ["one", "two"]
    editor.pattern_edit.setText("three")
    editor._add_text()
    assert editor.values() == ["one", "two", "three"]
    editor.list.item(0).setSelected(True)
    editor._remove_selected()
    assert editor.values() == ["two", "three"]
