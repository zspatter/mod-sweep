from pathlib import Path

from modsweep.manifest import Entry, Manifest
from modsweep.matcher import KEEP, KEEP_VERIFIED, META_ORPHAN, STALE, UNCLAIMED, match
from modsweep.scanner import DiskFile


def df(rel, size=100, subdir=""):
    name = rel.rsplit("/", 1)[-1]
    return DiskFile(
        path=Path("X:/dl") / rel, rel=rel, subdir=subdir, name=name, size=size, mtime_ns=0
    )


def man(label, *entries):
    return Manifest(label=label, source_path=Path("m"), entries=list(entries))


class StubCache:
    """Maps rel -> (xxh64_b64, crc32); mirrors HashCache.get's contract."""

    def __init__(self, data=None):
        self.data = data or {}

    def get(self, disk):
        return self.data.get(disk.rel)


def by_rel(results):
    return {r.disk.rel: r for r in results}


# --- without hashes -------------------------------------------------------


def test_name_size_match_keeps():
    m = man("L", Entry(file_name="a.7z", size=100))
    (r,) = match([df("a.7z")], [m])
    assert (r.status, r.claimed_by) == (KEEP, ["L"])


def test_size_mismatch_is_stale():
    m = man("L", Entry(file_name="a.7z", size=999))
    (r,) = match([df("a.7z")], [m])
    assert r.status == STALE


def test_unknown_name_is_unclaimed():
    (r,) = match([df("nobody.7z")], [man("L")])
    assert r.status == UNCLAIMED


def test_meta_inherits_base_status_and_orphan_detected():
    m = man("L", Entry(file_name="a.7z", size=100))
    results = by_rel(match([df("a.7z"), df("a.7z.meta", 28), df("gone.7z.meta", 28)], [m]))
    assert results["a.7z.meta"].status == KEEP
    assert results["a.7z.meta"].sidecar
    assert results["gone.7z.meta"].status == META_ORPHAN


# --- with cached hashes ---------------------------------------------------


def test_hash_match_verifies():
    m = man("L", Entry(file_name="a.7z", size=100, xxh64_b64="H1"))
    (r,) = match([df("a.7z")], [m], StubCache({"a.7z": ("H1", 1)}))
    assert (r.status, r.claimed_by) == (KEEP_VERIFIED, ["L"])


def test_hash_rescues_renamed_archive():
    m = man("L", Entry(file_name="original.7z", size=100, xxh64_b64="H1"))
    (r,) = match([df("renamed.7z")], [m], StubCache({"renamed.7z": ("H1", 1)}))
    assert r.status == KEEP_VERIFIED
    assert "original.7z" in r.note


def test_crc_rescue_via_nolvus_entry():
    m = man("N", Entry(file_name="original.7z", size_kb=1, crc32=0xAB))
    (r,) = match([df("renamed.7z", size=1024)], [m], StubCache({"renamed.7z": ("X", 0xAB)}))
    assert r.status == KEEP_VERIFIED


def test_hashless_source_still_claims_by_name_when_hash_known():
    m = man("[NoDelete] X", Entry(file_name="custom.7z"))
    (r,) = match([df("custom.7z")], [m], StubCache({"custom.7z": ("H9", 9)}))
    assert (r.status, r.claimed_by) == (KEEP, ["[NoDelete] X"])


def test_hash_contradiction_is_stale():
    m = man("L", Entry(file_name="a.7z", size=100, xxh64_b64="H1"))
    (r,) = match([df("a.7z")], [m], StubCache({"a.7z": ("OTHER", 2)}))
    assert r.status == STALE


def test_hashed_unknown_name_is_unclaimed():
    (r,) = match([df("nobody.7z")], [man("L")], StubCache({"nobody.7z": ("H", 1)}))
    assert r.status == UNCLAIMED


# --- platform edge cases --------------------------------------------------


def test_case_differing_files_both_survive():
    m = man("L", Entry(file_name="a.7z", size=100))
    results = match([df("a.7z"), df("A.7z")], [m])
    assert len(results) == 2


def test_meta_binds_case_insensitively():
    m = man("L", Entry(file_name="A.7z", size=100))
    results = by_rel(match([df("A.7z"), df("a.7z.meta", 28)], [m]))
    assert results["a.7z.meta"].status == KEEP


def test_subdir_mismatch_is_note_not_disqualifier():
    m = man("N", Entry(file_name="mod.zip", size_kb=1, subdir="1.1 OLD NAME"))
    (r,) = match([df("1.2 NEW/mod.zip", size=1024, subdir="1.2 NEW")], [m])
    assert r.status == KEEP
    assert "1.1 OLD NAME" in r.note
