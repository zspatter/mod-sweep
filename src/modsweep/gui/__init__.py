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

import sys

try:
    import PySide6.QtWidgets  # noqa: F401 - probe: the extra may be missing
except ImportError:  # pragma: no cover - exercised only without the extra
    print(
        "modsweep-gui requires the GUI dependencies.\n"
        '  installed tool:  uv tool install "modsweep[gui]"  '
        '(or pipx install "modsweep[gui]")\n'
        "  source checkout: uv sync --extra gui",
        file=sys.stderr,
    )
    raise

from .editor import ConfigEditorDialog
from .icons import _app_icon
from .window import MainWindow, main

__all__ = ["ConfigEditorDialog", "MainWindow", "_app_icon", "main"]
