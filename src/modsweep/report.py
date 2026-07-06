"""Console summary and CSV detail output for match results.

`summarize` composes four independently-testable sections: active sources,
the status table, per-source claim counts, and the largest candidates.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .manifest import Manifest
from .matcher import (
    KEEP,
    KEEP_VERIFIED,
    META_ORPHAN,
    STALE,
    UNCLAIMED,
    FileResult,
    status_order,
)

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


# --- data rows (consumed by the text renderers below and by the GUI) --------


def status_rows(results: list[FileResult]) -> list[tuple[str, int, int]]:
    """(status label, file count, total bytes) for each status, fixed order."""
    by_status: dict[str, list[FileResult]] = defaultdict(list)
    for r in results:
        by_status[r.status].append(r)
    return [
        (
            _LABELS[status],
            len(by_status.get(status, [])),
            sum(r.disk.size for r in by_status.get(status, [])),
        )
        for status in (KEEP_VERIFIED, KEEP, STALE, UNCLAIMED, META_ORPHAN)
    ]


def reclaim_bytes(results: list[FileResult]) -> int:
    return sum(r.disk.size for r in results if r.status in _CANDIDATE_STATUSES)


def claim_rows(results: list[FileResult]) -> list[tuple[str, int, int, int]]:
    """(source, claimed, unique, unique bytes), most-claimed first.

    Sidecars are excluded; unique = claimed by no other source, i.e. what
    retiring that source would free.
    """
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
    return [
        (label, count, unique_claims[label], unique_bytes[label])
        for label, count in claims.most_common()
    ]


def candidate_rows(
    results: list[FileResult], limit: int | None = None
) -> list[tuple[int, str, str]]:
    """(bytes, status, rel path) for non-sidecar candidates, largest first."""
    candidates = sorted(
        (r for r in results if r.status in (STALE, UNCLAIMED) and not r.sidecar),
        key=lambda r: r.disk.size,
        reverse=True,
    )
    if limit is not None:
        candidates = candidates[:limit]
    return [(r.disk.size, r.status, r.disk.rel) for r in candidates]


# --- text renderers ----------------------------------------------------------


def status_lines(results: list[FileResult]) -> list[str]:
    lines = [f"{'Status':<28} {'Files':>8} {'Size':>14}", "-" * 54]
    total_files = total_size = 0
    for label, count, size in status_rows(results):
        total_files += count
        total_size += size
        lines.append(f"{label:<28} {count:>8,} {_gb(size):>14}")
    lines.append("-" * 54)
    lines.append(f"{'Total':<28} {total_files:>8,} {_gb(total_size):>14}")
    lines.append("")
    lines.append(f"Potential reclaim (all candidates): {_gb(reclaim_bytes(results))}")
    return lines


def claim_lines(results: list[FileResult]) -> list[str]:
    rows = claim_rows(results)
    if not rows:
        return []
    width = max(len("source"), max(len(label) for label, *_ in rows))
    lines = [
        "Disk archives claimed per manifest"
        " (unique = claimed by no other source; what retiring it would free):",
        f"  {'source':<{width}} {'claimed':>8} {'unique':>8} {'unique size':>13}",
    ]
    for label, claimed, unique, unique_size in rows:
        lines.append(
            f"  {label:<{width}} {claimed:>8,} {unique:>8,} {_gb(unique_size):>13}"
        )
    return lines


def candidate_lines(results: list[FileResult], limit: int = 15) -> list[str]:
    rows = candidate_rows(results, limit)
    if not rows:
        return []
    lines = ["Largest deletion candidates:"]
    for size, status, rel in rows:
        lines.append(f"  {_gb(size):>12}  [{status}]  {rel}")
    return lines


def write_csv(results: list[FileResult], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rel_path", "status", "size_bytes", "claimed_by", "note"])
        for r in sorted(results, key=lambda r: (status_order(r.status), -r.disk.size)):
            writer.writerow(
                [r.disk.rel, r.status, r.disk.size, "; ".join(r.claimed_by), r.note]
            )


def _gb(size: int) -> str:
    return f"{size / (1 << 30):,.2f} GB"
