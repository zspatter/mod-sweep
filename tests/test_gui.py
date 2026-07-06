"""GUI smoke tests. Skipped entirely when the gui extra is not installed
(CI runs without PySide6); run headless via the offscreen platform."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from helpers import make_wabbajack, wj_hash  # noqa: E402
from modsweep.gui import MainWindow  # noqa: E402


def app():
    return QApplication.instance() or QApplication([])


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


def test_window_lists_sources_via_worker(tmp_path):
    app()
    win = MainWindow(build_config(tmp_path))
    wait_idle(win)  # refresh runs threaded now
    assert win.sources_list.count() == 1
    assert "A 1.0" in win.sources_list.item(0).text()
    assert "1 active source(s) loaded." in win.console.toPlainText()
    assert "Welcome to modsweep" in win.console.toPlainText()


def test_report_action_populates_tables(tmp_path):
    app()
    win = MainWindow(build_config(tmp_path))
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
    win = MainWindow(build_config(tmp_path))
    wait_idle(win)
    win.run_sweep(apply=False)
    wait_idle(win)
    text = win.console.toPlainText()
    assert "Sweep plan:" in text
    assert "Refused (hash never checked): 1" in text  # junk.7z is unhashed
    assert "Dry run - nothing moved." in text


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
    win = MainWindow(cfg)
    wait_idle(win)
    win.run_report()
    wait_idle(win)
    table = win.candidates_table
    assert table.rowCount() == 2
    table.sortItems(0)  # ascending by size: numeric, not lexicographic
    assert "small.7z" in table.item(0, 2).text()
    assert "big.7z" in table.item(1, 2).text()
