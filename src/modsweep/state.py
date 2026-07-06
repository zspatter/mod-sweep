"""Track the previously-active source set to detect silent vanishing.

Exclusions and supersessions are announced at resolution time, but a manifest
file that disappears from disk is simply never discovered - the one silent
way protection can lapse (e.g. uninstalling Wabbajack removes its
downloaded_mod_lists). The state file remembers which sources were active
after each config-driven run so the next run can warn instead of quietly
treating those archives as unclaimed.
"""

from __future__ import annotations

import json
from pathlib import Path

from .manifest import Manifest

STATE_NAME = "state.json"


def read(path: Path) -> dict[str, str]:
    """label -> source path recorded by the previous run ({} if none/corrupt)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    active = data.get("active", {})
    if not isinstance(active, dict):
        return {}
    return {str(k): str(v) for k, v in active.items()}


def write(path: Path, manifests: list[Manifest]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"active": {m.label: str(m.source_path) for m in manifests}}
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def vanished(
    previous: dict[str, str], current: list[Manifest]
) -> list[tuple[str, str]]:
    """(label, old_source_path) pairs whose manifest file no longer exists.

    A label that is inactive but whose source file still exists was dropped
    by exclusion/supersession - announced elsewhere - and is not reported.
    """
    active = {m.label for m in current}
    return [
        (label, source)
        for label, source in previous.items()
        if label not in active and not Path(source).exists()
    ]
