# PyInstaller spec building the console CLI and windowed GUI as a pair of
# standalone one-file executables. The CLI stays small (it never imports
# PySide6); the GUI carries Qt. Bundled manifests ship inside both.
#
#   uv run pyinstaller packaging/modsweep.spec
import sys
from pathlib import Path

spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent

datas = [(str(root / "src" / "modsweep" / "data"), "modsweep/data")]
icon = str(root / "assets" / "modsweep.ico") if sys.platform == "win32" else None


def build(entry: str, name: str, console: bool):
    analysis = Analysis(
        [str(spec_dir / entry)],
        pathex=[str(root / "src")],
        datas=datas,
        noarchive=False,
    )
    pyz = PYZ(analysis.pure)
    return EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        name=name,
        console=console,
        icon=icon,
        upx=False,
    )


cli_exe = build("cli_entry.py", "modsweep", console=True)
gui_exe = build("gui_entry.py", "modsweep-gui", console=False)
