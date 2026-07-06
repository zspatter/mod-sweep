import csv
from pathlib import Path

from modsweep.manifest import Manifest
from modsweep.matcher import KEEP, KEEP_VERIFIED, STALE, UNCLAIMED, FileResult
from modsweep.report import (
    candidate_lines,
    candidate_rows,
    claim_lines,
    claim_rows,
    reclaim_bytes,
    source_lines,
    status_lines,
    status_rows,
    summarize,
    write_csv,
)
from modsweep.scanner import DiskFile

GB = 1 << 30


def fr(rel, size, status, claimed_by=None, sidecar=False):
    name = rel.rsplit("/", 1)[-1]
    disk = DiskFile(
        path=Path("dl") / rel, rel=rel, subdir="", name=name, size=size, mtime_ns=0
    )
    return FileResult(disk, status, claimed_by or [], "", sidecar)


def test_status_lines_counts_sizes_and_reclaim():
    results = [
        fr("a.7z", 1 * GB, KEEP_VERIFIED),
        fr("b.7z", 2 * GB, UNCLAIMED),
        fr("c.7z", 1 * GB, STALE),
    ]
    text = "\n".join(status_lines(results))
    assert "Keep (hash verified)                1        1.00 GB" in text
    assert "Total                               3        4.00 GB" in text
    assert "Potential reclaim (all candidates): 3.00 GB" in text


def test_claim_rows_unique_attribution():
    results = [
        fr("a.7z", 1 * GB, KEEP, ["L1"]),  # unique to L1
        fr("b.7z", 1 * GB, KEEP, ["L1", "L2"]),  # shared
    ]
    assert claim_rows(results) == [
        ("L1", 2, 1, 1 * GB),
        ("L2", 1, 0, 0),
    ]


def test_claim_lines_put_source_first():
    results = [fr("a.7z", 1 * GB, KEEP, ["List With Spaces"])]
    lines = claim_lines(results)
    assert lines[1].split()[0] == "source"  # header order
    assert lines[2].strip().startswith("List With Spaces")


def test_status_rows_and_reclaim():
    results = [
        fr("a.7z", 1 * GB, KEEP_VERIFIED),
        fr("b.7z", 2 * GB, UNCLAIMED),
    ]
    rows = {label: (count, size) for label, count, size in status_rows(results)}
    assert rows["Keep (hash verified)"] == (1, 1 * GB)
    assert rows["Unclaimed (candidate)"] == (1, 2 * GB)
    assert reclaim_bytes(results) == 2 * GB


def test_candidate_rows_limit_none_returns_all():
    results = [fr(f"f{i}.7z", (i + 1) * GB, UNCLAIMED) for i in range(20)]
    assert len(candidate_rows(results)) == 20
    assert candidate_rows(results)[0][2] == "f19.7z"
    assert len(candidate_rows(results, limit=3)) == 3


def test_claim_lines_skip_sidecars_and_empty():
    sidecar = fr("a.7z.meta", 28, KEEP, ["L1"], sidecar=True)
    assert claim_lines([sidecar]) == []
    assert claim_lines([]) == []


def test_candidate_lines_sorted_capped_and_filtered():
    results = [fr(f"f{i}.7z", (i + 1) * GB, UNCLAIMED) for i in range(20)]
    results.append(fr("kept.7z", 99 * GB, KEEP, ["L"]))
    results.append(fr("x.7z.meta", 28, UNCLAIMED, sidecar=True))
    lines = candidate_lines(results)
    assert len(lines) == 1 + 15  # header + top 15
    assert "f19.7z" in lines[1]  # largest candidate first
    assert not any("kept.7z" in line for line in lines)
    assert not any(".meta" in line for line in lines)


def test_summarize_composes_sections():
    m = Manifest(label="List 1.0", source_path=Path("x") / "y" / "list.wabbajack")
    out = summarize([fr("a.7z", 1 * GB, UNCLAIMED)], [m])
    assert "Active sources: 1" in out
    assert "List 1.0" in out
    assert "Potential reclaim" in out
    assert "Largest deletion candidates:" in out
    assert "Disk archives claimed" not in out  # nothing claimed


def test_write_csv_contents_and_ordering(tmp_path):
    results = [
        fr("small-unclaimed.7z", 1 * GB, UNCLAIMED),
        fr("big-unclaimed.7z", 2 * GB, UNCLAIMED, ),
        fr("kept.7z", 1 * GB, KEEP, ["L1", "L2"]),
    ]
    out = tmp_path / "report.csv"
    write_csv(results, out)
    with open(out, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["rel_path"] for r in rows] == [
        "kept.7z",  # sorted by status, then size descending within status
        "big-unclaimed.7z",
        "small-unclaimed.7z",
    ]
    assert rows[0]["claimed_by"] == "L1; L2"
    assert rows[1]["size_bytes"] == str(2 * GB)


def test_source_lines_show_origin_tail():
    m = Manifest(
        label="List 1.0",
        source_path=Path("deep") / "nested" / "dir" / "list.wabbajack",
    )
    text = "\n".join(source_lines([m]))
    assert "list.wabbajack" in text
    assert "deep" not in text  # only the last three path parts shown
