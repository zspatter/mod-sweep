"""Console summary and CSV detail output for match results."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .manifest import Manifest
from .matcher import KEEP, KEEP_VERIFIED, META_ORPHAN, STALE, UNCLAIMED, FileResult

_LABELS = {
    KEEP_VERIFIED: "Keep (hash verified)",
    KEEP: "Keep (name+size match)",
    STALE: "Stale version (candidate)",
    UNCLAIMED: "Unclaimed (candidate)",
    META_ORPHAN: "Orphan .meta (candidate)",
}


def _gb(size: int) -> str:
    return f"{size / (1 << 30):,.2f} GB"


def summarize(results: list[FileResult], manifests: list[Manifest]) -> str:
    lines: list[str] = []
    lines.append(f"Active sources: {len(manifests)} (every one whitelists its files)")
    for m in manifests:
        origin = str(Path(*m.source_path.parts[-3:]))
        lines.append(f"  - {m.label}  ({len(m.entries)} entries)  [{origin}]")
    lines.append("  Retire a list: delete its .wabbajack, or add an `exclude` glob")
    lines.append("  (label or file name) in modsweep.toml / --exclude.")
    lines.append("")

    by_status: dict[str, list[FileResult]] = defaultdict(list)
    for r in results:
        by_status[r.status].append(r)

    lines.append(f"{'Status':<28} {'Files':>8} {'Size':>14}")
    lines.append("-" * 54)
    total_size = 0
    for status in (KEEP_VERIFIED, KEEP, STALE, UNCLAIMED, META_ORPHAN):
        group = by_status.get(status, [])
        size = sum(r.disk.size for r in group)
        total_size += size
        lines.append(f"{_LABELS[status]:<28} {len(group):>8,} {_gb(size):>14}")
    lines.append("-" * 54)
    lines.append(f"{'Total':<28} {len(results):>8,} {_gb(total_size):>14}")

    reclaim = sum(
        r.disk.size for r in results if r.status in (STALE, UNCLAIMED, META_ORPHAN)
    )
    lines.append("")
    lines.append(f"Potential reclaim (all candidates): {_gb(reclaim)}")

    claims: Counter[str] = Counter()
    unique_claims: Counter[str] = Counter()
    unique_bytes: Counter[str] = Counter()
    for r in results:
        if r.sidecar:
            continue
        for label in r.claimed_by:
            claims[label] += 1
        if len(r.claimed_by) == 1:
            unique_claims[r.claimed_by[0]] += 1
            unique_bytes[r.claimed_by[0]] += r.disk.size
    if claims:
        lines.append("")
        lines.append(
            "Disk archives claimed per manifest"
            " (unique = claimed by no other source; what retiring it would free):"
        )
        lines.append(f"  {'claimed':>8} {'unique':>8} {'unique size':>13}  source")
        for label, count in claims.most_common():
            lines.append(
                f"  {count:>8,} {unique_claims[label]:>8,}"
                f" {_gb(unique_bytes[label]):>13}  {label}"
            )

    candidates = sorted(
        (r for r in results if r.status in (STALE, UNCLAIMED) and not r.sidecar),
        key=lambda r: r.disk.size,
        reverse=True,
    )
    if candidates:
        lines.append("")
        lines.append("Largest deletion candidates:")
        for r in candidates[:15]:
            lines.append(f"  {_gb(r.disk.size):>12}  [{r.status}]  {r.disk.rel}")

    return "\n".join(lines)


def write_csv(results: list[FileResult], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rel_path", "status", "size_bytes", "claimed_by", "note"])
        for r in sorted(results, key=lambda r: (r.status, -r.disk.size)):
            writer.writerow(
                [r.disk.rel, r.status, r.disk.size, "; ".join(r.claimed_by), r.note]
            )
