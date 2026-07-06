"""Parse Nolvus installer manifests (InstallPackage.xml).

Layout: <Softwares> holds shared tools whose archives land in the downloads
root; <Categories>/<Category> names map 1:1 to subdirectories under the
downloads root (e.g. "1.1 SKSE PLUGINS") and hold the mod archives.

<Size> is round(bytes / 1024), so it is only a sanity check; <CRC32> is the
authoritative identity (verified against real files on disk).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .manifest import Entry, Manifest


def load(path: Path) -> Manifest:
    path = Path(path)
    if path.name.lower().endswith(".xml.gz"):
        # Bundled manifests ship gzipped: the raw XML runs ~70 MB.
        import gzip

        with gzip.open(path, "rb") as fh:
            root = ET.parse(fh).getroot()
    else:
        root = ET.parse(path).getroot()

    guide = root.find("./Settings/Guide")
    name = (guide.findtext("Name") if guide is not None else None) or "Nolvus"
    version = (guide.findtext("Version") if guide is not None else None) or ""
    label = f"{name} {version}".strip()

    entries: list[Entry] = []
    for file_el in root.iterfind("./Softwares/Soft/Files/File"):
        entry = _entry(file_el, subdir="", kind="tool")
        if entry is not None:
            entries.append(entry)
    for cat in root.iterfind("./Categories/Category"):
        subdir = (cat.findtext("Name") or "").strip()
        for file_el in cat.iterfind("./Mods/Mod/Files/File"):
            entry = _entry(file_el, subdir=subdir, kind="mod")
            if entry is not None:
                entries.append(entry)
    return Manifest(
        label=label, source_path=path, entries=entries, name=name, version=version
    )


def _entry(file_el: ET.Element, subdir: str, kind: str) -> Entry | None:
    file_name = (file_el.findtext("FileName") or "").strip()
    if not file_name:
        return None
    size_text = (file_el.findtext("Size") or "").strip()
    crc_text = (file_el.findtext("CRC32") or "").strip()
    return Entry(
        file_name=file_name,
        subdir=subdir,
        size_kb=int(size_text) if size_text.isdigit() else None,
        crc32=int(crc_text, 16) if crc_text else None,
        kind=kind,
    )
