import os

from modsweep.cache import HashCache
from modsweep.scanner import scan


def disk_file(dl, name):
    return next(f for f in scan(dl) if f.name == name)


def test_roundtrip_and_invalidation(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    f = dl / "a.7z"
    f.write_bytes(b"12345")
    cache = HashCache(tmp_path / "c.sqlite")

    disk = disk_file(dl, "a.7z")
    assert cache.get(disk) is None
    cache.put(disk, "HASH=", 42)
    assert cache.get(disk) == ("HASH=", 42)

    # Same size, different mtime: stale entry must not be served.
    os.utime(f, ns=(disk.mtime_ns + 10**9, disk.mtime_ns + 10**9))
    assert cache.get(disk_file(dl, "a.7z")) is None

    # Different size invalidates too.
    f.write_bytes(b"123456")
    assert cache.get(disk_file(dl, "a.7z")) is None
    cache.close()


def test_put_replaces_existing_row(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"x")
    cache = HashCache(tmp_path / "c.sqlite")
    disk = disk_file(dl, "a.7z")
    cache.put(disk, "OLD=", 1)
    cache.put(disk, "NEW=", 2)
    assert cache.get(disk) == ("NEW=", 2)
    cache.close()


def test_cache_persists_across_instances(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"x")
    disk = disk_file(dl, "a.7z")
    cache = HashCache(tmp_path / "c.sqlite")
    cache.put(disk, "H=", 7)
    cache.close()
    reopened = HashCache(tmp_path / "c.sqlite")
    assert reopened.get(disk) == ("H=", 7)
    reopened.close()


def test_snapshot_matches_get_including_invalidation(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"12345")
    (dl / "b.7z").write_bytes(b"6789")
    cache = HashCache(tmp_path / "c.sqlite")
    a, b = disk_file(dl, "a.7z"), disk_file(dl, "b.7z")
    cache.put(a, "HA=", 1)
    cache.put(b, "HB=", 2)

    snap = cache.snapshot()
    assert snap.get(a) == ("HA=", 1)
    assert snap.get(b) == ("HB=", 2)

    # Stale rows are refused just like HashCache.get.
    os.utime(dl / "a.7z", ns=(a.mtime_ns + 10**9, a.mtime_ns + 10**9))
    assert snap.get(disk_file(dl, "a.7z")) is None
    cache.close()


def test_snapshot_is_point_in_time(tmp_path):
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"x")
    cache = HashCache(tmp_path / "c.sqlite")
    disk = disk_file(dl, "a.7z")
    snap = cache.snapshot()  # taken before the put
    cache.put(disk, "H=", 7)
    assert snap.get(disk) is None
    assert cache.snapshot().get(disk) == ("H=", 7)
    cache.close()


def test_two_connections_share_one_cache(tmp_path):
    """Two CLI processes can point at the same cache: WAL lets the second
    connection read while the first is mid-write-session."""
    dl = tmp_path / "dl"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"aa")
    (dl / "b.7z").write_bytes(b"bb")
    a, b = disk_file(dl, "a.7z"), disk_file(dl, "b.7z")

    first = HashCache(tmp_path / "c.sqlite")
    second = HashCache(tmp_path / "c.sqlite")
    first.put(a, "HA=", 1)
    assert second.get(a) == ("HA=", 1)  # committed writes visible across
    second.put(b, "HB=", 2)
    assert first.get(b) == ("HB=", 2)
    assert first.snapshot().get(a) == ("HA=", 1)
    first.close()
    second.close()
