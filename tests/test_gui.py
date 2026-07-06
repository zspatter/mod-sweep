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


def test_window_builds_and_lists_sources(tmp_path):
    app()
    win = MainWindow(build_config(tmp_path))
    assert win.sources_list.count() == 1
    assert "A 1.0" in win.sources_list.item(0).text()
    assert "1 active source(s) loaded." in win.console.toPlainText()


def test_report_action_renders_summary(tmp_path):
    app()
    win = MainWindow(build_config(tmp_path))
    win.run_report()
    assert win._worker.wait(10_000)
    win._worker = None  # let the finished signal settle without the loop
    QApplication.processEvents()
    text = win.console.toPlainText()
    assert "Active sources: 1" in text
    assert "Unclaimed (candidate)" in text


def test_sweep_dry_run_reports_refusal(tmp_path):
    app()
    win = MainWindow(build_config(tmp_path))
    win.run_sweep(apply=False)
    assert win._worker.wait(10_000)
    QApplication.processEvents()
    text = win.console.toPlainText()
    assert "Sweep plan:" in text
    assert "Refused (hash never checked): 1" in text  # junk.7z is unhashed
    assert "Dry run - nothing moved." in text
