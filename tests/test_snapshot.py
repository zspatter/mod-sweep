import json
from pathlib import Path

from modsweep import snapshot
from modsweep.manifest import Entry, Manifest


def sample_manifest():
    return Manifest(
        label="Some List 1.2.3",
        source_path=Path("orig.wabbajack"),
        entries=[
            Entry(file_name="a.7z", size=100, xxh64_b64="H1", kind="mod"),
            Entry(file_name="b.zip", subdir="1.1 X", size_kb=10, crc32=0xAB, kind="tool"),
        ],
        name="Some List",
        version="1.2.3",
        machine="some_list",
    )


def test_save_load_roundtrip(tmp_path):
    path = snapshot.save(sample_manifest(), tmp_path)
    loaded = snapshot.load(path)
    assert loaded.label == "Some List 1.2.3"
    assert loaded.entries == sample_manifest().entries
    assert (loaded.name, loaded.version, loaded.machine) == (
        "Some List", "1.2.3", "some_list"
    )


def test_is_snapshot_sniff(tmp_path):
    snap = snapshot.save(sample_manifest(), tmp_path)
    assert snapshot.is_snapshot(snap)
    modlist = tmp_path / "modlist.json"
    modlist.write_text(json.dumps({"Name": "X", "Archives": []}), encoding="utf-8")
    assert not snapshot.is_snapshot(modlist)
    assert not snapshot.is_snapshot(tmp_path / "missing.json")


def test_load_rejects_non_snapshot(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}", encoding="utf-8")
    try:
        snapshot.load(p)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_slug_filenames_are_safe(tmp_path):
    m = sample_manifest()
    m.label = "Weird: List / Name? 2.0"
    path = snapshot.save(m, tmp_path)
    assert path.name == "Weird_List_Name_2.0.json"
