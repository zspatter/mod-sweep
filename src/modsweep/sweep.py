"""Quarantine-based sweep: move deletion candidates out of the downloads dir.

Nothing is ever hard-deleted. Candidates move to a timestamped batch folder
under the quarantine directory (keep it on the same volume as downloads so
moves are instant renames), preserving relative paths. A sweep-manifest.csv
is written incrementally as files move, and `restore` uses it to put a whole
batch back.

Safety rules:
- Only stale-version / unclaimed / orphan-.meta files are eligible.
- An archive whose hash was never checked against the whitelist is refused
  (run `modsweep hash --only-candidates` first).
- A .meta sidecar moves only together with its archive.
"""

from __future__ import annotations

import csv
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .cache import HashCache
from .matcher import META_ORPHAN, STALE, UNCLAIMED, FileResult

CANDIDATE_STATUSES = (STALE, UNCLAIMED, META_ORPHAN)
MANIFEST_NAME = "sweep-manifest.csv"
_COLUMNS = ["rel_path", "original_path", "status", "size_bytes", "claimed_by", "note"]


@dataclass
class Plan:
    ready: list[FileResult]
    refused: list[FileResult]  # candidates whose hash was never checked

    @property
    def ready_bytes(self) -> int:
        return sum(r.disk.size for r in self.ready)

    @property
    def refused_bytes(self) -> int:
        return sum(r.disk.size for r in self.refused)


def plan(results: list[FileResult], cache: HashCache) -> Plan:
    ready: list[FileResult] = []
    refused: list[FileResult] = []
    refused_bases: set[str] = set()
    metas: list[FileResult] = []
    for r in results:
        if r.status not in CANDIDATE_STATUSES:
            continue
        if r.disk.is_meta:
            metas.append(r)
        elif cache.get(r.disk) is None:
            refused.append(r)
            refused_bases.add(r.disk.rel.lower())
        else:
            ready.append(r)
    for r in metas:  # sidecars follow their archive, including its refusal
        if r.disk.base_rel.lower() in refused_bases:
            refused.append(r)
        else:
            ready.append(r)
    return Plan(ready=ready, refused=refused)


def execute(p: Plan, quarantine: Path) -> Path:
    """Move planned files into a new timestamped batch; return the batch dir."""
    batch = Path(quarantine) / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    batch.mkdir(parents=True, exist_ok=False)
    with open(batch / MANIFEST_NAME, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(_COLUMNS)
        for r in p.ready:
            dest = batch / r.disk.rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(r.disk.path), str(dest))
            writer.writerow(
                [r.disk.rel, str(r.disk.path), r.status, r.disk.size,
                 "; ".join(r.claimed_by), r.note]
            )
            fh.flush()  # a crash mid-batch must not lose the restore record
    return batch


@dataclass
class Batch:
    path: Path
    created: datetime
    files: int
    size: int


def list_batches(quarantine: Path) -> list[Batch]:
    """Enumerate sweep batches under the quarantine dir.

    Only directories carrying a sweep manifest count — anything else in the
    quarantine dir is never touched. Age comes from the batch dir's
    timestamp name, falling back to filesystem mtime.
    """
    quarantine = Path(quarantine)
    if not quarantine.is_dir():
        return []
    out: list[Batch] = []
    for d in sorted(quarantine.iterdir()):
        if not d.is_dir() or not (d / MANIFEST_NAME).exists():
            continue
        try:
            created = datetime.strptime(d.name, "%Y-%m-%d_%H%M%S")
        except ValueError:
            created = datetime.fromtimestamp(d.stat().st_mtime)
        files = size = 0
        for f in d.rglob("*"):
            if f.is_file():
                files += 1
                size += f.stat().st_size
        out.append(Batch(path=d, created=created, files=files, size=size))
    return out


def purge_batch(batch: Batch | Path) -> None:
    """Delete a quarantine batch permanently. The only hard delete in the tool
    (`sweep --delete` composes execute + purge_batch rather than adding one)."""
    path = batch.path if isinstance(batch, Batch) else Path(batch)
    shutil.rmtree(path)


def restore(batch: Path) -> tuple[int, int, int]:
    """Move a batch back to its original locations.

    Returns (moved, skipped_existing, missing). Files whose original path is
    now occupied are left in quarantine rather than overwritten.
    """
    batch = Path(batch)
    manifest = batch / MANIFEST_NAME
    if not manifest.exists():
        raise SystemExit(f"error: {manifest} not found — not a sweep batch")
    moved = skipped = missing = 0
    with open(manifest, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        src = batch / row["rel_path"]
        dest = Path(row["original_path"])
        if not src.exists():
            missing += 1
            continue
        if dest.exists():
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        moved += 1
    return moved, skipped, missing
