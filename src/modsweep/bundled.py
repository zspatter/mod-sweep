"""Locate bundled and user-downloaded Nolvus manifests.

`nolvus = ['bundled']` in the config resolves to the manifests shipped
inside the package plus any later downloads in the per-user data dir.
Packaged installs (wheel, standalone executable) must not be written to,
so `update-manifests` downloads land in the user dir - both are searched.
"""

from __future__ import annotations

import os
import sys
from importlib import resources
from pathlib import Path

KEYWORD = "bundled"


def package_dir() -> Path | None:
    """The manifests shipped inside the installed package, if any."""
    try:
        root = Path(str(resources.files("modsweep") / "data" / "nolvus"))
    except (ModuleNotFoundError, TypeError):  # pragma: no cover - defensive
        return None
    return root if root.is_dir() else None


def user_dir() -> Path:
    """Per-user, always-writable home for downloaded manifest updates."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "modsweep" / "manifests" / "nolvus"


def manifest_dirs() -> list[Path]:
    """Every directory the `bundled` keyword stands for (existing only)."""
    dirs = [d for d in (package_dir(), user_dir()) if d is not None and d.is_dir()]
    return dirs


def known_names() -> set[str]:
    """File names already present across all bundled locations."""
    return {p.name for d in manifest_dirs() for p in d.iterdir() if p.is_file()}
