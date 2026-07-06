"""Shared builders for synthetic manifests and MO2 installs."""

from __future__ import annotations

import base64
import json
import zipfile
import zlib
from pathlib import Path

import xxhash


def wj_hash(data: bytes) -> str:
    """Wabbajack-style hash: base64 of the little-endian xxHash64 digest."""
    digest = xxhash.xxh64(data).intdigest().to_bytes(8, "little")
    return base64.b64encode(digest).decode("ascii")


def make_wabbajack(path: Path, name: str, version: str, archives) -> Path:
    """archives: iterable of (file_name, size, xxh64_b64)."""
    data = {
        "Name": name,
        "Version": version,
        "Archives": [
            {
                "Name": file_name,
                "Size": size,
                "Hash": xxh64_b64,
                "State": {"$type": "NexusDownloader, Wabbajack.Lib"},
            }
            for file_name, size, xxh64_b64 in archives
        ],
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modlist", json.dumps(data))
    return path


def make_nolvus(path: Path, guide="Guide", version="1.0", tools=(), categories=None) -> Path:
    """tools: [(file_name, content_bytes)]; categories: {name: [(file_name, bytes)]}.

    Sizes and CRCs are derived from the content, mirroring the real format
    (Size = round(bytes / 1024), CRC32 of the whole file).
    """

    def file_el(file_name: str, data: bytes) -> str:
        return (
            f"<File><FileName>{file_name}</FileName>"
            f"<Size>{round(len(data) / 1024)}</Size>"
            f"<CRC32>{zlib.crc32(data):08X}</CRC32></File>"
        )

    softs = "".join(
        f"<Soft><Files>{file_el(n, d)}</Files></Soft>" for n, d in tools
    )
    cats = "".join(
        f"<Category><Name>{cat}</Name><Mods><Mod><Files>"
        + "".join(file_el(n, d) for n, d in files)
        + "</Files></Mod></Mods></Category>"
        for cat, files in (categories or {}).items()
    )
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><InstallationManifest>'
        f"<Settings><Guide><Name>{guide}</Name><Version>{version}</Version></Guide></Settings>"
        f"<Softwares>{softs}</Softwares><Categories>{cats}</Categories>"
        "</InstallationManifest>",
        encoding="utf-8",
    )
    return path


def make_mo2_install(root: Path, instance: str, mods: dict[str, str | None]) -> Path:
    """mods: {folder_name: installationFile or None (no archive recorded)}."""
    mods_dir = root / instance / "mods"
    for folder, archive in mods.items():
        d = mods_dir / folder
        d.mkdir(parents=True)
        lines = ["[General]"]
        if archive is not None:
            lines.append(f"installationFile={archive}")
        (d / "meta.ini").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root / instance
