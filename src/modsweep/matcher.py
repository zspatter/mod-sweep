"""Match disk files against the union of manifest whitelists.

Matching is by file name (case-insensitive), deliberately ignoring which
subdirectory the file sits in: Nolvus renumbers/renames its category folders
between guide versions, so location is reported as a note, never used to
disqualify a match.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from .cache import HashCache
from .manifest import Entry, Manifest
from .scanner import DiskFile

log = logging.getLogger(__name__)

KEEP_VERIFIED = "keep-verified"  # name + hash match
KEEP = "keep"  # name + size match, hash not yet computed
STALE = "stale-version"  # name matches an entry, but size or hash does not
UNCLAIMED = "unclaimed"  # no manifest references this file name
META_ORPHAN = "meta-orphan"  # .meta sidecar whose archive is gone

_STATUS_ORDER = [KEEP_VERIFIED, KEEP, STALE, UNCLAIMED, META_ORPHAN]


@dataclass
class FileResult:
    disk: DiskFile
    status: str
    claimed_by: list[str] = field(default_factory=list)  # manifest labels
    note: str = ""
    sidecar: bool = False


def status_order(status: str) -> int:
    return _STATUS_ORDER.index(status) if status in _STATUS_ORDER else len(_STATUS_ORDER)


class _Index:
    """Manifest entries indexed by name and by hash.

    Wabbajack identifies archives purely by hash — files on disk may carry
    rewritten names (e.g. a sha256 embedded in the file name) — so a hash hit
    counts as claimed even when no entry shares the file name.
    """

    def __init__(self, manifests: list[Manifest]):
        self.by_name: dict[str, list[tuple[str, Entry]]] = defaultdict(list)
        self.by_xxh64: dict[str, list[tuple[str, Entry]]] = defaultdict(list)
        self.by_crc32: dict[int, list[tuple[str, Entry]]] = defaultdict(list)
        for manifest in manifests:
            for entry in manifest.entries:
                self.by_name[entry.file_name.lower()].append((manifest.label, entry))
                if entry.xxh64_b64 is not None:
                    self.by_xxh64[entry.xxh64_b64].append((manifest.label, entry))
                if entry.crc32 is not None:
                    self.by_crc32[entry.crc32].append((manifest.label, entry))

    def hash_hits(self, xxh64_b64: str, crc32: int) -> list[tuple[str, Entry]]:
        return self.by_xxh64.get(xxh64_b64, []) + self.by_crc32.get(crc32, [])


def match(
    files: list[DiskFile],
    manifests: list[Manifest],
    cache: HashCache | None = None,
) -> list[FileResult]:
    start = time.perf_counter()
    index = _Index(manifests)
    results: dict[str, FileResult] = {}  # keyed by exact rel — case matters on POSIX
    lower_rel: dict[str, str] = {}  # case-insensitive lookup for sidecar binding
    metas: list[DiskFile] = []
    for disk in files:
        if disk.is_meta:
            metas.append(disk)
            continue
        results[disk.rel] = _classify(disk, index, cache)
        lower_rel.setdefault(disk.rel.lower(), disk.rel)

    out = list(results.values())
    for disk in metas:
        base = results.get(disk.base_rel)
        if base is None:
            key = lower_rel.get(disk.base_rel.lower())
            base = results.get(key) if key is not None else None
        if base is None:
            out.append(FileResult(disk, META_ORPHAN, note="no archive next to this .meta", sidecar=True))
        else:
            out.append(
                FileResult(
                    disk,
                    base.status,
                    claimed_by=list(base.claimed_by),
                    note=f"sidecar of {disk.base_rel}",
                    sidecar=True,
                )
            )
    log.info(
        "matched %d files against %d sources in %.2fs",
        len(out), len(manifests), time.perf_counter() - start,
    )
    return out


def _classify(
    disk: DiskFile,
    index: _Index,
    cache: HashCache | None,
) -> FileResult:
    candidates = index.by_name.get(disk.name.lower(), [])
    cached = cache.get(disk) if cache is not None else None
    if cached is not None:
        xxh64_b64, crc32 = cached
        return _classify_hashed(disk, candidates, index, xxh64_b64, crc32)
    return _classify_unhashed(disk, candidates)


def _classify_hashed(
    disk: DiskFile,
    candidates: list[tuple[str, Entry]],
    index: _Index,
    xxh64_b64: str,
    crc32: int,
) -> FileResult:
    hash_matches = [
        (label, e) for label, e in candidates if e.matches_hash(xxh64_b64, crc32)
    ]
    if hash_matches:
        return FileResult(
            disk,
            KEEP_VERIFIED,
            sorted({label for label, _ in hash_matches}),
            _location_note(disk, hash_matches),
        )
    rescued = index.hash_hits(xxh64_b64, crc32)
    if rescued:
        names = sorted({e.file_name for _, e in rescued})
        return FileResult(
            disk,
            KEEP_VERIFIED,
            sorted({label for label, _ in rescued}),
            f"hash matches manifest entry named {names[0]}",
        )
    # Hashless sources ([NoDelete] custom additions, MO2-install recovery)
    # can still claim by name — there is nothing to verify against.
    hashless = [
        (label, e)
        for label, e in candidates
        if e.matches_hash(xxh64_b64, crc32) is None and e.matches_size(disk.size)
    ]
    if hashless:
        return FileResult(
            disk,
            KEEP,
            sorted({label for label, _ in hashless}),
            _join("claimed by name-only source", _location_note(disk, hashless)),
        )
    if not candidates:
        return FileResult(disk, UNCLAIMED, note="hash matches no manifest entry")
    # Name is known to some list, but this exact file is not.
    return FileResult(
        disk,
        STALE,
        note=_join("hash matches no manifest entry", _location_note(disk, candidates)),
    )


def _classify_unhashed(
    disk: DiskFile, candidates: list[tuple[str, Entry]]
) -> FileResult:
    if not candidates:
        return FileResult(disk, UNCLAIMED)
    size_matches = [(label, e) for label, e in candidates if e.matches_size(disk.size)]
    if size_matches:
        return FileResult(
            disk,
            KEEP,
            sorted({label for label, _ in size_matches}),
            _location_note(disk, size_matches),
        )
    return FileResult(
        disk,
        STALE,
        note=_join(
            f"name matches {len(candidates)} entr{'y' if len(candidates) == 1 else 'ies'} but size differs",
            _location_note(disk, candidates),
        ),
    )


def _location_note(disk: DiskFile, matched: list[tuple[str, Entry]]) -> str:
    expected = {e.subdir for _, e in matched}
    if disk.subdir in expected:
        return ""
    shown = sorted(s or "<root>" for s in expected)
    return f"expected in {', '.join(shown[:3])}"


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)
