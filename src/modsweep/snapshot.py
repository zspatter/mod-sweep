"""Export/load compact manifest snapshots.

A snapshot is a small JSON file carrying everything matching needs (name,
size, hashes, subdir, kind per entry), so a whitelist survives deletion of
the original .wabbajack or InstallPackage.xml. Snapshots load as ordinary
manifest sources.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .manifest import Entry, Manifest

FORMAT_KEY = "modsweep_snapshot"
FORMAT_VERSION = 1


def save(manifest: Manifest, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = {
        # FORMAT_KEY must stay the first key: is_snapshot() sniffs the head
        # of the file to distinguish snapshots from bare modlist.json.
        FORMAT_KEY: FORMAT_VERSION,
        "label": manifest.label,
        "name": manifest.name,
        "version": manifest.version,
        "machine": manifest.machine,
        "source": str(manifest.source_path),
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": [
            {
                "name": e.file_name,
                "subdir": e.subdir,
                "size": e.size,
                "size_kb": e.size_kb,
                "xxh64": e.xxh64_b64,
                "crc32": e.crc32,
                "kind": e.kind,
            }
            for e in manifest.entries
        ],
    }
    path = out_dir / (_slug(manifest.label) + ".json")
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return path


def load(path: Path) -> Manifest:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if FORMAT_KEY not in data:
        raise ValueError(f"{path}: not a modsweep snapshot")
    entries = [
        Entry(
            file_name=e["name"],
            subdir=e.get("subdir", ""),
            size=e.get("size"),
            size_kb=e.get("size_kb"),
            xxh64_b64=e.get("xxh64"),
            crc32=e.get("crc32"),
            kind=e.get("kind", "mod"),
        )
        for e in data.get("entries", [])
    ]
    return Manifest(
        label=data["label"],
        source_path=path,
        entries=entries,
        name=data.get("name", ""),
        version=data.get("version", ""),
        machine=data.get("machine", ""),
    )


def is_snapshot(path: Path) -> bool:
    """Cheap sniff to distinguish a snapshot from other .json files."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return FORMAT_KEY in fh.read(256)
    except OSError:
        return False


def _slug(label: str) -> str:
    return re.sub(r"[^\w.-]+", "_", label).strip("_")
