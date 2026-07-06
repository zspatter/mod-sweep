"""Console summary and CSV detail output for match results.

`summarize` composes four independently-testable sections: active sources,
the status table, per-source claim counts, and the largest candidates.
"""

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
_CANDIDATE_STATUSES = (STALE, UNCLAIMED, META_ORPHAN)


def summarize(results: list[FileResult], manifests: list[Manifest]) -> str:
    lines = source_lines(manifests)
    lines.append("")
    lines.extend(status_lines(results))
    claims = claim_lines(results)
    if claims:
        lines.append("")
        lines.extend(claims)
    candidates = candidate_lines(results)
    if candidates:
        lines.append("")
        lines.extend(candidates)
    return "\n".join(lines)


def source_lines(manifests: list[Manifest]) -> list[str]:
    lines = [f"Active sources: {len(manifests)} (every one whitelists its files)"]
    for m in manifests:
        origin = str(Path(*m.source_path.parts[-3:]))
        lines.append(f"  - {m.label}  ({len(m.entries)} entries)  [{origin}]")
    lines.append("  Retire a list: delete its .wabbajack, or add an `exclude` glob")
    lines.append("  (label or file name) in modsweep.toml / --exclude.")
    return lines


def status_lines(results: list[FileResult]) -> list[str]:
    by_status: dict[str, list[FileResult]] = defaultdict(list)
    for r in results:
        by_status[r.status].append(r)

    lines = [f"{'Status':<28} {'Files':>8} {'Size':>14}", "-" * 54]
    total_size = 0
    for status in (KEEP_VERIFIED, KEEP, STALE, UNCLAIMED, META_ORPHAN):
        group = by_status.get(status, [])
        size = sum(r.disk.size for r in group)
        total_size += size
        lines.append(f"{_LABELS[status]:<28} {len(group):>8,} {_gb(size):>14}")
    lines.append("-" * 54)
    lines.append(f"{'Total':<28} {len(results):>8,} {_gb(total_size):>14}")

    reclaim = sum(r.disk.size for r in results if r.status in _CANDIDATE_STATUSES)
    lines.append("")
    lines.append(f"Potential reclaim (all candidates): {_gb(reclaim)}")
    return lines


def claim_lines(results: list[FileResult]) -> list[str]:
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
    if not claims:
        return []
    lines = [
        "Disk archives claimed per manifest"
        " (unique = claimed by no other source; what retiring it would free):",
        f"  {'claimed':>8} {'unique':>8} {'unique size':>13}  source",
    ]
    for label, count in claims.most_common():
        lines.append(
            f"  {count:>8,} {unique_claims[label]:>8,}"
            f" {_gb(unique_bytes[label]):>13}  {label}"
        )
    return lines


def candidate_lines(results: list[FileResult], limit: int = 15) -> list[str]:
    candidates = sorted(
        (r for r in results if r.status in (STALE, UNCLAIMED) and not r.sidecar),
        key=lambda r: r.disk.size,
        reverse=True,
    )
    if not candidates:
        return []
    lines = ["Largest deletion candidates:"]
    for r in candidates[:limit]:
        lines.append(f"  {_gb(r.disk.size):>12}  [{r.status}]  {r.disk.rel}")
    return lines


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


def _gb(size: int) -> str:
    return f"{size / (1 << 30):,.2f} GB"
