import base64
import zlib

import xxhash

from modsweep.hashutil import CHUNK, hash_file


def test_hash_file_matches_reference_encodings(tmp_path):
    data = b"modsweep test vector"
    p = tmp_path / "f.bin"
    p.write_bytes(data)
    xxh64_b64, crc32 = hash_file(p)
    assert crc32 == zlib.crc32(data)
    expected = base64.b64encode(
        xxhash.xxh64(data).intdigest().to_bytes(8, "little")
    ).decode("ascii")
    assert xxh64_b64 == expected


def test_hash_file_streams_across_chunks(tmp_path):
    data = b"AB" * (CHUNK // 2 + 512)  # spans more than one read
    p = tmp_path / "big.bin"
    p.write_bytes(data)
    xxh64_b64, crc32 = hash_file(p)
    assert crc32 == zlib.crc32(data)
    whole = base64.b64encode(
        xxhash.xxh64(data).intdigest().to_bytes(8, "little")
    ).decode("ascii")
    assert xxh64_b64 == whole


def test_empty_file(tmp_path):
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    xxh64_b64, crc32 = hash_file(p)
    assert crc32 == 0
    assert xxh64_b64  # xxh64 of empty input is still a valid digest


def test_read_ahead_survives_many_tiny_chunks(tmp_path, monkeypatch):
    """Stress the one-read-in-flight handoff: hundreds of chunk boundaries
    must produce identical digests to a single-shot hash."""
    import modsweep.hashutil as hashutil

    monkeypatch.setattr(hashutil, "CHUNK", 7)
    data = bytes(range(256)) * 5
    p = tmp_path / "many.bin"
    p.write_bytes(data)
    xxh64_b64, crc32 = hashutil.hash_file(p)
    assert crc32 == zlib.crc32(data)
    expected = base64.b64encode(
        xxhash.xxh64(data).intdigest().to_bytes(8, "little")
    ).decode("ascii")
    assert xxh64_b64 == expected
