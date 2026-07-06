"""Cache parsed manifests keyed by the source file's identity.

Parsing is the expensive resolution step (a .wabbajack holds a 30-70 MB
JSON); this pickle cache turns repeat loads into milliseconds. Entries
invalidate when the source's size or mtime changes - the same contract as
the hash cache. Only file-backed sources are cached: MO2 installs are
directories whose mtime does not reflect deep meta.ini edits, and they are
cheap to rescan anyway.
"""

from __future__ import annotations

import hashlib
import logging
import pickle
from pathlib import Path

from .manifest import Manifest

log = logging.getLogger(__name__)


def load(cache_dir: Path, source: Path, kind: str) -> Manifest | None:
    """The cached parse of `source`, or None when absent/stale/corrupt."""
    try:
        stat = Path(source).stat()
        with open(_entry_path(cache_dir, source, kind), "rb") as fh:
            cached = pickle.load(fh)
        if (
            cached["size"] == stat.st_size
            and cached["mtime_ns"] == stat.st_mtime_ns
        ):
            log.debug("manifest cache hit: %s", source)
            return cached["manifest"]
    except Exception:
        # Best-effort cache: any trouble means a fresh parse. Unpickling
        # after a refactor can raise AttributeError/ImportError/TypeError,
        # not just PickleError, so the catch is deliberately broad.
        pass
    return None


def store(cache_dir: Path, source: Path, kind: str, manifest: Manifest) -> None:
    try:
        stat = Path(source).stat()
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "manifest": manifest,
        }
        with open(_entry_path(cache_dir, source, kind), "wb") as fh:
            pickle.dump(payload, fh)
    except OSError:
        log.debug("manifest cache store failed for %s", source)


def _entry_path(cache_dir: Path, source: Path, kind: str) -> Path:
    digest = hashlib.sha1(f"{kind}|{source}".encode("utf-8", "replace")).hexdigest()
    return Path(cache_dir) / f"{digest}.pkl"
