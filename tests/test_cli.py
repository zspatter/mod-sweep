import json
import zipfile

from modsweep.cli import _expand_wabbajack, load_manifests


def make_wj(path, name, version):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("modlist", json.dumps({"Name": name, "Version": version, "Archives": []}))


def test_expand_wabbajack_pins_explicit_files_not_dir_walks(tmp_path):
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    make_wj(wj_dir / "found.wabbajack", "Found", "1.0")
    explicit = tmp_path / "explicit.wabbajack"
    make_wj(explicit, "Explicit", "1.0")

    sources = _expand_wabbajack([wj_dir, explicit])
    assert ("wabbajack", wj_dir / "found.wabbajack", False) in sources
    assert ("wabbajack", explicit, True) in sources


def test_explicit_entry_pins_version_through_latest_only(tmp_path):
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    make_wj(wj_dir / "old.wabbajack", "X", "1.0")
    make_wj(wj_dir / "new.wabbajack", "X", "2.0")

    implicit_only = _expand_wabbajack([wj_dir])
    labels = {m.label for m in load_manifests(implicit_only, latest_only=True)}
    assert labels == {"X 2.0"}

    with_pin = _expand_wabbajack([wj_dir, wj_dir / "old.wabbajack"])
    labels = {m.label for m in load_manifests(with_pin, latest_only=True)}
    assert labels == {"X 1.0", "X 2.0"}


def test_pin_sticks_across_label_dedupe(tmp_path):
    """The dir walk loads a label first; a later explicit entry for the same
    file must still pin it."""
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    make_wj(wj_dir / "old.wabbajack", "X", "1.0")
    make_wj(wj_dir / "new.wabbajack", "X", "2.0")

    sources = _expand_wabbajack([wj_dir]) + _expand_wabbajack([wj_dir / "old.wabbajack"])
    labels = {m.label for m in load_manifests(sources, latest_only=True)}
    assert labels == {"X 1.0", "X 2.0"}
