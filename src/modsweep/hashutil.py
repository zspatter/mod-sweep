"""Streaming file hashing: xxHash64 (Wabbajack) and CRC32 (Nolvus) in one pass.

Wabbajack stores hashes as base64 of the little-endian 8-byte xxHash64 digest.
"""

from __future__ import annotations

import base64
import zlib
from pathlib import Path

import xxhash

CHUNK = 4 << 20


def hash_file(path: Path) -> tuple[str, int]:
    """Return (xxh64_b64, crc32) for a file, reading it once."""
    xh = xxhash.xxh64()
    crc = 0
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            xh.update(chunk)
            crc = zlib.crc32(chunk, crc)
    xxh64_b64 = base64.b64encode(xh.intdigest().to_bytes(8, "little")).decode("ascii")
    return xxh64_b64, crc
