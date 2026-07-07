# PyInstaller spec building the console CLI and windowed GUI into one
# onedir folder with a shared _internal (no duplicated Qt). Onedir skips
# the self-extract-at-launch behavior desktop AV heuristics dislike, and
# starts faster for it.
#
# On Windows the spec ALSO emits single-file portable exes (dist/*.exe),
# zipped individually as the -gui and -cli release assets. Those are the
# NexusMods files (GUI as the main download - mod users are GUI-forward;
# CLI as the optional companion). Nexus flags new unsigned binaries for
# review regardless of packaging (both onefile and onedir were reviewed).
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


def build_portable(analysis, name: str, console: bool):
    """A self-contained single-file exe (everything inside; extracts to a
    temp dir at launch)."""
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


if sys.platform == "win32":
    # dist/modsweep.exe and dist/modsweep-gui.exe land next to the
    # dist/modsweep/ onedir folder (distinct names on Windows; skipped
    # elsewhere, where a bare 'modsweep' file would collide with the dir).
    build_portable(cli_analysis, "modsweep", console=True)
    build_portable(gui_analysis, "modsweep-gui", console=False)
