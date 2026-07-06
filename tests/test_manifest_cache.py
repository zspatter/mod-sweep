import os

from helpers import make_wabbajack

from modsweep import manifest_cache
from modsweep.cli import _expand_wabbajack, load_manifests
from modsweep.wabbajack import load as load_wj


def test_store_load_roundtrip_and_invalidation(tmp_path):
    wj = make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    cache_dir = tmp_path / "manifest_cache"
    parsed = load_wj(wj)

    assert manifest_cache.load(cache_dir, wj, "wabbajack") is None  # cold
    manifest_cache.store(cache_dir, wj, "wabbajack", parsed)
    cached = manifest_cache.load(cache_dir, wj, "wabbajack")
    assert cached is not None and cached.label == "A 1.0"

    # kind participates in the key
    assert manifest_cache.load(cache_dir, wj, "nolvus") is None

    # touching the source invalidates
    bumped = wj.stat().st_mtime_ns + 10**9
    os.utime(wj, ns=(bumped, bumped))
    assert manifest_cache.load(cache_dir, wj, "wabbajack") is None


def test_corrupt_cache_entry_is_tolerated(tmp_path):
    wj = make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    cache_dir = tmp_path / "manifest_cache"
    manifest_cache.store(cache_dir, wj, "wabbajack", load_wj(wj))
    (entry,) = cache_dir.iterdir()
    entry.write_bytes(b"not a pickle")
    assert manifest_cache.load(cache_dir, wj, "wabbajack") is None


def test_load_manifests_skips_reparsing_via_cache(tmp_path, monkeypatch):
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    sources = _expand_wabbajack([tmp_path])
    cache_dir = tmp_path / "manifest_cache"

    (first,) = load_manifests(sources, parse_cache=cache_dir)
    assert first.label == "A 1.0"

    def boom(_path):
        raise AssertionError("parser called despite a warm cache")

    monkeypatch.setitem(
        __import__("modsweep.cli", fromlist=["_LOADERS"])._LOADERS, "wabbajack", boom
    )
    (second,) = load_manifests(sources, parse_cache=cache_dir)
    assert second.label == "A 1.0"


def test_no_cache_dir_means_no_caching(tmp_path):
    make_wabbajack(tmp_path / "a.wabbajack", "A", "1.0", [])
    sources = _expand_wabbajack([tmp_path])
    (loaded,) = load_manifests(sources)  # parse_cache omitted: plain parse
    assert loaded.label == "A 1.0"
    assert not (tmp_path / "manifest_cache").exists()
