"""SQLite cache of file hashes, keyed by path and invalidated on size/mtime change."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .scanner import DiskFile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hashes (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    xxh64_b64 TEXT NOT NULL,
    crc32 INTEGER NOT NULL
)
"""


class HashCache:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, disk: DiskFile) -> tuple[str, int] | None:
        row = self._conn.execute(
            "SELECT size, mtime_ns, xxh64_b64, crc32 FROM hashes WHERE path = ?",
            (str(disk.path),),
        ).fetchone()
        if row is None:
            return None
        size, mtime_ns, xxh64_b64, crc32 = row
        if size != disk.size or mtime_ns != disk.mtime_ns:
            return None
        return xxh64_b64, crc32

    def put(self, disk: DiskFile, xxh64_b64: str, crc32: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO hashes (path, size, mtime_ns, xxh64_b64, crc32)"
            " VALUES (?, ?, ?, ?, ?)",
            (str(disk.path), disk.size, disk.mtime_ns, xxh64_b64, crc32),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
