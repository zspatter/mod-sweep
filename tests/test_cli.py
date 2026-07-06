import json
import zipfile
import zlib

import pytest

from modsweep import sweep as sweep_mod
from modsweep.cli import _expand_wabbajack, load_manifests, main


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


# --- end-to-end through main() --------------------------------------------


def e2e_fixture(tmp_path):
    """Synthetic downloads tree + Nolvus manifest claiming only keep.7z."""
    dl = tmp_path / "downloads"
    dl.mkdir()
    keep = dl / "keep.7z"
    keep.write_bytes(b"K" * 100)
    (dl / "old.7z").write_bytes(b"O" * 100)
    xml = tmp_path / "manifest.xml"
    xml.write_text(
        f"""<?xml version="1.0" encoding="utf-8"?>
<InstallationManifest>
  <Settings><Guide><Name>Fixture</Name><Version>1.0</Version></Guide></Settings>
  <Softwares><Soft><Files><File>
    <FileName>keep.7z</FileName>
    <Size>{round(100 / 1024)}</Size>
    <CRC32>{zlib.crc32(keep.read_bytes()):08X}</CRC32>
  </File></Files></Soft></Softwares>
  <Categories/>
</InstallationManifest>
""",
        encoding="utf-8",
    )
    common = [
        "--downloads", str(dl),
        "-m", str(xml),
        "--cache", str(tmp_path / "cache.sqlite"),
    ]
    return dl, common


def test_sweep_delete_requires_apply(tmp_path):
    dl, common = e2e_fixture(tmp_path)
    with pytest.raises(SystemExit):
        main(["sweep", *common, "--delete"])


def test_sweep_delete_purges_immediately(tmp_path):
    dl, common = e2e_fixture(tmp_path)
    quarantine = tmp_path / "quarantine"
    assert main(["hash", *common]) == 0
    assert main(
        ["sweep", *common, "--quarantine", str(quarantine), "--apply", "--delete"]
    ) == 0
    assert (dl / "keep.7z").exists()
    assert not (dl / "old.7z").exists()
    assert sweep_mod.list_batches(quarantine) == []  # nothing left to restore


def test_sweep_without_delete_leaves_restorable_batch(tmp_path):
    dl, common = e2e_fixture(tmp_path)
    quarantine = tmp_path / "quarantine"
    assert main(["hash", *common]) == 0
    assert main(["sweep", *common, "--quarantine", str(quarantine), "--apply"]) == 0
    (batch,) = sweep_mod.list_batches(quarantine)
    assert main(["restore", str(batch.path)]) == 0
    assert (dl / "old.7z").exists()


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
