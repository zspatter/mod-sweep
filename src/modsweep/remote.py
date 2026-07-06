"""Fetch new bundled manifests and check for app updates.

GitHub is the only server involved: the contents API lists the repo's
manifest directory (raw URLs deliver the files), and the releases API
carries the latest app version. Manifest downloads land in the per-user
data dir - packaged installs are read-only - which the `bundled` config
keyword already searches.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import bundled
from .manifest import version_key

log = logging.getLogger(__name__)

REPO = "zspatter/mod-sweep"
MANIFEST_INDEX_URL = (
    f"https://api.github.com/repos/{REPO}/contents/src/modsweep/data/nolvus?ref=main"
)
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
_TIMEOUT = 15


def _get(url: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "modsweep", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read()


@dataclass(frozen=True)
class RemoteManifest:
    name: str
    download_url: str


def available_manifests(get=_get) -> list[RemoteManifest]:
    """Manifest files currently published in the repository."""
    entries = json.loads(get(MANIFEST_INDEX_URL))
    return [
        RemoteManifest(name=e["name"], download_url=e["download_url"])
        for e in entries
        if e.get("type") == "file"
        and e["name"].lower().endswith((".xml", ".xml.gz"))
    ]


def update_manifests(get=_get) -> list[str]:
    """Download manifests not present locally into the user data dir.

    Returns the downloaded file names (empty when already up to date).
    """
    have = bundled.known_names()
    new = [r for r in available_manifests(get) if r.name not in have]
    if not new:
        return []
    dest = bundled.user_dir()
    dest.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for remote_file in new:
        (dest / remote_file.name).write_bytes(get(remote_file.download_url))
        log.info("downloaded manifest %s", remote_file.name)
        downloaded.append(remote_file.name)
    return downloaded


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    url: str


def check_update(current: str, get=_get) -> UpdateInfo | None:
    """The newer release, or None when this version is current (or ahead)."""
    release = json.loads(get(LATEST_RELEASE_URL))
    latest = str(release.get("tag_name", "")).lstrip("v")
    if not latest or version_key(latest) <= version_key(current):
        return None
    return UpdateInfo(
        current=current,
        latest=latest,
        url=release.get("html_url") or RELEASES_PAGE,
    )
