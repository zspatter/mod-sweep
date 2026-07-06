"""Load modsweep.toml — the authoritative record of active sources.

The config is the durable statement of which lists are active: retiring a
list means removing its line and rerunning `report`/`sweep`. Source types are
declared explicitly (wabbajack / nolvus / installs / recovery) so nothing is
auto-detected from a config entry — important for setups where installs are
spread across drives or live next to unrelated folders. CLI arguments
override config values. Relative paths resolve against the config file's
directory.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_NAME = "modsweep.toml"


@dataclass
class Config:
    downloads: Path | None = None
    cache: Path | None = None
    wabbajack: list[Path] = field(default_factory=list)  # files or dirs to search
    nolvus: list[Path] = field(default_factory=list)  # InstallPackage.xml files
    installs: list[Path] = field(default_factory=list)  # MO2 installs -> [NoDelete]
    recovery: list[Path] = field(default_factory=list)  # installs whitelisted whole
    snapshots: list[Path] = field(default_factory=list)  # exported snapshot JSONs
    exclude: list[str] = field(default_factory=list)  # globs vs label or file name
    latest_only: bool = False  # keep only the newest version of each list
    quarantine: Path | None = None
    quarantine_keep_days: int | None = None  # purge batches older than this

    @property
    def has_sources(self) -> bool:
        return bool(
            self.wabbajack or self.nolvus or self.installs or self.recovery or self.snapshots
        )


def load(path: Path | None) -> Config:
    """Load config from `path`, or ./modsweep.toml if present, else empty."""
    if path is None:
        path = Path(DEFAULT_NAME)
        if not path.exists():
            return Config()
    elif not path.exists():
        raise SystemExit(f"error: config not found: {path}")

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.resolve().parent

    def resolve(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else base / p

    def resolve_list(key: str) -> list[Path]:
        return [resolve(v) for v in data.get(key, [])]

    quarantine_data = data.get("quarantine") or {}
    quarantine = quarantine_data.get("dir")
    keep_days = quarantine_data.get("keep_days")
    return Config(
        downloads=resolve(data["downloads"]) if "downloads" in data else None,
        cache=resolve(data["cache"]) if "cache" in data else None,
        wabbajack=resolve_list("wabbajack"),
        nolvus=resolve_list("nolvus"),
        installs=resolve_list("installs"),
        recovery=resolve_list("recovery"),
        snapshots=resolve_list("snapshots"),
        exclude=[str(v) for v in data.get("exclude", [])],
        latest_only=bool(data.get("latest_only", False)),
        quarantine=resolve(quarantine) if quarantine else None,
        quarantine_keep_days=int(keep_days) if keep_days is not None else None,
    )
