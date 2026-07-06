"""Grab GUI screenshots for the README and mod pages (offscreen).

    uv run python packaging/make_screenshots.py [config.toml]

Writes docs/screenshots/*.png using the given config (default: the local
modsweep.toml), so the shots show a real inventory.
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from modsweep.gui import ConfigEditorDialog, MainWindow


def _load_omit_terms() -> tuple[str, ...]:
    """Substrings of list labels to hide from published screenshots.

    They come from a gitignored sidecar (packaging/screenshots-omit.txt,
    one substring per line) so the names themselves never enter the public
    repo. Rows are hidden from the rendered widgets only - matching runs
    with every source active, so hidden lists' uniquely-claimed archives
    cannot leak into the candidates table under their own names either.
    """
    sidecar = Path(__file__).with_name("screenshots-omit.txt")
    if not sidecar.exists():
        return ()
    return tuple(
        line.strip().lower()
        for line in sidecar.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )


OMIT_TERMS = _load_omit_terms()


def load_fonts(app) -> None:
    """The offscreen platform on Windows has no GDI font access - feed it
    the system fonts directly or every glyph renders as tofu."""
    fonts_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
        candidate = fonts_dir / name
        if candidate.exists():
            QFontDatabase.addApplicationFont(str(candidate))
    app.setFont(QFont("Segoe UI", 9))


def scrub_private_rows(window) -> None:
    tree = window.sources_list
    for i in reversed(range(tree.topLevelItemCount())):
        label = tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0].lower()
        if any(term in label for term in OMIT_TERMS):
            tree.takeTopLevelItem(i)  # children (older versions) go with it
    claims = window.claims_table
    for row in reversed(range(claims.rowCount())):
        if any(t in claims.item(row, 0).text().lower() for t in OMIT_TERMS):
            claims.removeRow(row)
    candidates = window.candidates_table
    for row in reversed(range(candidates.rowCount())):
        if any(t in candidates.item(row, 2).text().lower() for t in OMIT_TERMS):
            candidates.removeRow(row)
    # keep the headline source count consistent with the visible tree
    import re

    window.summary_label.setText(
        re.sub(
            r"across <b>\d+</b> sources",
            f"across <b>{tree.topLevelItemCount()}</b> sources",
            window.summary_label.text(),
        )
    )


def settle(window, app) -> None:
    while window._worker is not None and window._worker.isRunning():
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()


def main() -> int:
    app = QApplication([])
    load_fonts(app)
    out = Path(__file__).resolve().parents[1] / "docs" / "screenshots"
    out.mkdir(parents=True, exist_ok=True)
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None

    app.setStyle("Fusion")  # style whose palette we can drive headless
    light_palette = app.palette()
    shots = {}
    for palette, suffix in ((light_palette, "light"), (_dark_palette(), "dark")):
        app.setPalette(palette)
        window = MainWindow(config_path, show_welcome=False)
        window.show_result_popups = False
        window.resize(1200, 750)
        window.show()
        settle(window, app)  # sources loaded
        window.run_report()
        settle(window, app)
        scrub_private_rows(window)
        app.processEvents()
        report = window.grab()
        report.save(str(out / f"report-{suffix}.png"))

        dialog = ConfigEditorDialog(window.cfg)
        dialog.resize(780, 580)
        dialog.show()
        for _ in range(20):
            app.processEvents()
        editor = dialog.grab()
        editor.save(str(out / f"config-editor-{suffix}.png"))
        dialog.close()
        window.close()
        shots[suffix] = (report, editor)

    # Side-by-side light|dark composites keep the README filenames stable;
    # the per-mode files serve pages wanting one theme (Nexus takes dark).
    _stitch(shots["light"][0], shots["dark"][0], out / "report.png")
    _stitch(shots["light"][1], shots["dark"][1], out / "config-editor.png")
    print(f"wrote light/dark pairs and composites to {out}")
    return 0


def _dark_palette():
    """Fusion dark palette: the offscreen platform ignores OS color-scheme
    hints, so the dark capture drives the palette directly (script-level
    only - the app itself still follows the system theme)."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    window, base, text = QColor(53, 53, 53), QColor(35, 35, 35), QColor(222, 222, 222)
    for role, color in (
        (QPalette.ColorRole.Window, window),
        (QPalette.ColorRole.WindowText, text),
        (QPalette.ColorRole.Base, base),
        (QPalette.ColorRole.AlternateBase, QColor(45, 45, 45)),
        (QPalette.ColorRole.Text, text),
        (QPalette.ColorRole.Button, window),
        (QPalette.ColorRole.ButtonText, text),
        (QPalette.ColorRole.ToolTipBase, base),
        (QPalette.ColorRole.ToolTipText, text),
        (QPalette.ColorRole.PlaceholderText, QColor(150, 150, 150)),
        (QPalette.ColorRole.Highlight, QColor(42, 130, 218)),
        (QPalette.ColorRole.HighlightedText, QColor(240, 240, 240)),
        (QPalette.ColorRole.Link, QColor(90, 160, 255)),
    ):
        palette.setColor(role, color)
    return palette


def _stitch(left, right, path: Path) -> None:
    from PySide6.QtGui import QImage, QPainter

    gap = 12
    image = QImage(
        left.width() + right.width() + gap,
        max(left.height(), right.height()),
        QImage.Format.Format_ARGB32,
    )
    image.fill(0)  # transparent seam
    painter = QPainter(image)
    painter.drawPixmap(0, 0, left)
    painter.drawPixmap(left.width() + gap, 0, right)
    painter.end()
    image.save(str(path))


if __name__ == "__main__":
    raise SystemExit(main())
