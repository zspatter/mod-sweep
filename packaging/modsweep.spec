# PyInstaller spec building the console CLI and windowed GUI into one
# onedir folder with a shared _internal (no duplicated Qt). Onedir avoids
# the self-extracting onefile bootloader that antivirus heuristics (and
# NexusMods' scanner) routinely flag on unsigned builds.
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
    exe = EXE(
        pyz,
        analysis.scripts,
        name=name,
        console=console,
        icon=icon,
        exclude_binaries=True,
        upx=False,
    )
    return exe, analysis


cli_exe, cli_analysis = build("cli_entry.py", "modsweep", console=True)
gui_exe, gui_analysis = build("gui_entry.py", "modsweep-gui", console=False)

COLLECT(
    cli_exe,
    cli_analysis.binaries,
    cli_analysis.datas,
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    name="modsweep",
    upx=False,
)
