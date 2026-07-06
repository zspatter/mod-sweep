"""Walk a downloads directory and inventory its files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

META_SUFFIX = ".meta"


@dataclass(frozen=True)
class DiskFile:
    path: Path
    rel: str  # relative to the downloads root; '/'-separated on all platforms
    subdir: str  # '' for the root
    name: str
    size: int
    mtime_ns: int

    @property
    def is_meta(self) -> bool:
        return self.name.lower().endswith(META_SUFFIX)

    @property
    def base_rel(self) -> str:
        """For .meta sidecars: the rel path of the archive they annotate."""
        return self.rel[: -len(META_SUFFIX)]


def scan(root: Path) -> list[DiskFile]:
    root = Path(root)
    out: list[DiskFile] = []

    def walk(directory: Path, subdir: str) -> None:
        with os.scandir(directory) as it:
            for entry in it:
                if entry.is_dir(follow_symlinks=False):
                    child = entry.name if not subdir else f"{subdir}/{entry.name}"
                    walk(Path(entry.path), child)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat()
                    rel = entry.name if not subdir else f"{subdir}/{entry.name}"
                    out.append(
                        DiskFile(
                            path=Path(entry.path),
                            rel=rel,
                            subdir=subdir,
                            name=entry.name,
                            size=st.st_size,
                            mtime_ns=st.st_mtime_ns,
                        )
                    )

    walk(root, "")
    return out
