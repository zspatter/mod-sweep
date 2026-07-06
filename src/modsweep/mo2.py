"""Whitelist custom additions from MO2 instances.

User-added mods live in MO2 mod folders prefixed with `[NoDelete]`
(usually `[NoDelete] 00.000 {mod name}`). Each mod folder's meta.ini records
the archive it was installed from (`installationFile`); those archives are
protected in the downloads directory even though no modlist manifest claims
them. Separator folders (`..._separator`) have no archive and are skipped.

Entries carry no size or hash, so they match by file name alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .manifest import Entry, Manifest

NODELETE_PREFIX = "[nodelete]"


def load(path: Path, include_all: bool = False) -> Manifest:
    """Load from an MO2 instance dir (containing mods/) or a mods dir itself.

    By default only `[NoDelete]` mods are read (the user's custom additions).
    With include_all, every installed mod's archive is collected — a
    name-only recovery whitelist for lists whose .wabbajack manifest is gone.
    """
    path = Path(path)
    mods_dir = path if path.name.lower() == "mods" else _find_mods_dir(path)
    if mods_dir is None:
        raise ValueError(f"{path}: no MO2 mods directory found")
    # Nolvus wraps a whole portable instance in a container named MODS
    # (MODS/{mods,profiles,overwrite,downloads,...}): what we found may be
    # that container rather than the real mods dir - descend when the
    # instance markers say so.
    inner = _find_mods_dir(mods_dir)
    if inner is not None and _has_instance_markers(mods_dir):
        mods_dir = inner
    instance_dir = mods_dir.parent
    if instance_dir.name.lower() == "mods":  # the container, not a name
        instance_dir = instance_dir.parent
    instance = instance_dir.name or str(instance_dir)

    entries, missing = _collect_entries(mods_dir, include_all)
    if missing:
        print(
            f"warning: {instance}: {len(missing)} mod(s) have no "
            f"installationFile recorded (e.g. {missing[0]})",
            file=sys.stderr,
        )
    label = f"MO2 install {instance}" if include_all else f"[NoDelete] {instance}"
    # No version: each instance is its own group under latest-only filtering.
    return Manifest(label=label, source_path=mods_dir, entries=entries, name=label)


def _collect_entries(
    mods_dir: Path, include_all: bool
) -> tuple[list[Entry], list[str]]:
    """Gather archive entries from mod folders; also return folders that
    record no source archive (in-place creations with nothing to protect)."""
    entries: list[Entry] = []
    missing: list[str] = []
    for mod_dir in sorted(mods_dir.iterdir()):
        if not _wanted(mod_dir, include_all):
            continue
        archive = _installation_file(mod_dir / "meta.ini")
        if archive:
            entries.append(Entry(file_name=_base_name(archive), kind="custom"))
        else:
            missing.append(mod_dir.name)
    return entries, missing


def _wanted(mod_dir: Path, include_all: bool) -> bool:
    if not mod_dir.is_dir():
        return False
    name = mod_dir.name.lower()
    if name.endswith("_separator"):
        return False
    return include_all or name.startswith(NODELETE_PREFIX)


def _base_name(archive: str) -> str:
    # meta.ini may hold a full Windows-style path even when read on POSIX;
    # split on both separators rather than trusting Path.
    return archive.replace("\\", "/").rsplit("/", 1)[-1]


def has_nodelete_mods(path: Path) -> bool:
    mods_dir = path if path.name.lower() == "mods" else _find_mods_dir(path)
    if mods_dir is None:
        return False
    return any(
        d.is_dir() and d.name.lower().startswith(NODELETE_PREFIX)
        for d in mods_dir.iterdir()
    )


def _find_mods_dir(instance: Path) -> Path | None:
    for candidate in instance.iterdir():
        if candidate.is_dir() and candidate.name.lower() == "mods":
            return candidate
    return None


def _has_instance_markers(directory: Path) -> bool:
    """True when a directory looks like an MO2 instance root itself
    (profiles/overwrite/downloads siblings next to its mods dir)."""
    markers = {"profiles", "overwrite", "downloads"}
    return any(
        child.is_dir() and child.name.lower() in markers
        for child in directory.iterdir()
    )


def _installation_file(meta_ini: Path) -> str | None:
    if not meta_ini.exists():
        return None
    section = ""
    for line in meta_ini.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
        elif section == "general" and line.lower().startswith("installationfile="):
            value = line.split("=", 1)[1].strip()
            return value or None
    return None
