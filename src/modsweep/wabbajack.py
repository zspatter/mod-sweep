"""Parse Wabbajack modlists (.wabbajack archives or a bare modlist.json)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from .manifest import Entry, Manifest

_GAME_SOURCE = "GameFileSourceDownloader"


def load(path: Path) -> Manifest:
    path = Path(path)
    if path.suffix.lower() == ".wabbajack":
        data = _read_modlist_json(path)
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    name = data.get("Name") or path.stem
    version = data.get("Version") or ""
    # Old-format lists ship a placeholder Version; the gallery .metadata
    # sidecar next to the .wabbajack carries the real one.
    if version in ("", "0.0.1.0"):
        version = _sidecar_version(path) or version
    label = f"{name} {version}".strip()

    entries: list[Entry] = []
    for arc in data.get("Archives", []):
        state_type = (arc.get("State") or {}).get("$type", "")
        kind = "game" if state_type.startswith(_GAME_SOURCE) else "mod"
        entries.append(
            Entry(
                file_name=arc["Name"],
                subdir="",
                size=arc.get("Size"),
                xxh64_b64=arc.get("Hash"),
                kind=kind,
            )
        )
    return Manifest(
        label=label, source_path=path, entries=entries, name=name, version=version
    )


def _sidecar_version(path: Path) -> str | None:
    sidecar = path.with_name(path.name + ".metadata")
    if not sidecar.exists():
        return None
    try:
        return json.loads(sidecar.read_text(encoding="utf-8")).get("version")
    except (OSError, json.JSONDecodeError):
        return None


def _read_modlist_json(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        target = next(
            (n for n in zf.namelist() if n.lower() in ("modlist", "modlist.json")),
            None,
        )
        if target is None:
            raise ValueError(f"{path}: no modlist entry inside archive")
        with zf.open(target) as fh:
            return json.load(fh)
