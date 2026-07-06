"""Common manifest model shared by the Wabbajack and Nolvus parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Entry:
    """A single archive a modlist expects to find in the downloads directory."""

    file_name: str
    subdir: str = ""  # '' means the downloads root; Nolvus mods live in category subdirs
    size: int | None = None  # exact size in bytes (Wabbajack)
    size_kb: int | None = None  # size as round(bytes / 1024) (Nolvus)
    xxh64_b64: str | None = None  # base64 of little-endian xxHash64 digest (Wabbajack)
    crc32: int | None = None  # CRC32 of the whole file (Nolvus)
    kind: str = "mod"  # mod | tool | game

    def matches_size(self, size: int) -> bool:
        if self.size is not None:
            return self.size == size
        if self.size_kb is not None:
            # Nolvus sizes are rounded to KB; allow 1 KB of slack on top of rounding.
            return abs(self.size_kb - size / 1024) <= 1.5
        return True

    def matches_hash(self, xxh64_b64: str | None, crc32: int | None) -> bool | None:
        """True/False when the entry carries a comparable hash, None when it doesn't."""
        if self.xxh64_b64 is not None and xxh64_b64 is not None:
            return self.xxh64_b64 == xxh64_b64
        if self.crc32 is not None and crc32 is not None:
            return self.crc32 == crc32
        return None


@dataclass
class Manifest:
    label: str  # e.g. "Gate to Sovngarde 33.0.0" or "Nolvus Awakening 6.0.20"
    source_path: Path
    entries: list[Entry] = field(default_factory=list)
    name: str = ""  # list identity without version, e.g. "LoreRim"
    version: str = ""
    machine: str = ""  # stable machine id (Wabbajack machineURL) when known

    @property
    def group_key(self) -> str:
        """Version-grouping identity: machine id when available (robust to
        list renames between releases), else name, else label."""
        return (self.machine or self.name or self.label).lower()


def version_key(version: str) -> list[tuple[int, int, str]]:
    """Sortable key for dotted version strings; tolerant of suffixes.

    Numeric components compare numerically ("10.0" > "9.9"); a component
    like "3b" sorts right after 3; purely textual components sort after
    numeric ones. An empty version sorts lowest.
    """
    key: list[tuple[int, int, str]] = []
    for part in re.split(r"[.\-_+ ]+", version.strip()):
        if not part:
            continue
        m = re.match(r"(\d+)(.*)", part)
        if m:
            key.append((0, int(m.group(1)), m.group(2)))
        else:
            key.append((1, 0, part))
    return key


def latest_only(
    manifests: list[Manifest],
    pinned: frozenset[str] | set[str] = frozenset(),
) -> tuple[
    list[Manifest], list[tuple[Manifest, Manifest]], list[tuple[Manifest, Manifest]]
]:
    """Keep only the newest version of each list, grouped by list name.

    `pinned` labels (explicitly-listed sources) are never dropped, but they
    still compete as versions - pinning the newest of a group does not
    resurrect older ones. Returns (kept, superseded, pinned_kept) where the
    pair lists hold (manifest, group_winner). Sources without a version
    (e.g. [NoDelete] instances) form single-member groups and always survive.
    """
    winners: dict[str, Manifest] = {}
    for m in manifests:
        current = winners.get(m.group_key)
        if current is None or version_key(m.version) > version_key(current.version):
            winners[m.group_key] = m
    kept: list[Manifest] = []
    superseded: list[tuple[Manifest, Manifest]] = []
    pinned_kept: list[tuple[Manifest, Manifest]] = []
    for m in manifests:
        winner = winners[m.group_key]
        if m is winner:
            kept.append(m)
        elif m.label in pinned:
            kept.append(m)
            pinned_kept.append((m, winner))
        else:
            superseded.append((m, winner))
    return kept, superseded, pinned_kept
