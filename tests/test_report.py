from pathlib import Path

from modsweep.manifest import Manifest
from modsweep.matcher import KEEP, KEEP_VERIFIED, STALE, UNCLAIMED, FileResult
from modsweep.report import candidate_lines, claim_lines, source_lines, status_lines, summarize
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


def test_claim_lines_unique_attribution():
    results = [
        fr("a.7z", 1 * GB, KEEP, ["L1"]),  # unique to L1
        fr("b.7z", 1 * GB, KEEP, ["L1", "L2"]),  # shared
    ]
    lines = claim_lines(results)
    l1_row = next(line for line in lines if line.endswith("L1"))
    l2_row = next(line for line in lines if line.endswith("L2"))
    assert "2" in l1_row and "1.00 GB" in l1_row  # 2 claimed, 1 unique / 1 GB
    assert l2_row.split()[:2] == ["1", "0"]  # 1 claimed, 0 unique


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


def test_source_lines_show_origin_tail():
    m = Manifest(
        label="List 1.0",
        source_path=Path("deep") / "nested" / "dir" / "list.wabbajack",
    )
    text = "\n".join(source_lines([m]))
    assert "list.wabbajack" in text
    assert "deep" not in text  # only the last three path parts shown
