import json
import zipfile

from modsweep import wabbajack


def make_wj(path, name="Test List", version="1.2.3", archives=None, entry_name="modlist"):
    data = {"Name": name, "Version": version, "Archives": archives or []}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(entry_name, json.dumps(data))


def test_load_archive_entries_and_kinds(tmp_path):
    wj = tmp_path / "list.wabbajack"
    make_wj(
        wj,
        archives=[
            {
                "Name": "Mod-1.7z",
                "Size": 123,
                "Hash": "abc=",
                "State": {"$type": "NexusDownloader, Wabbajack.Lib"},
            },
            {
                "Name": "game.bsa",
                "Size": 5,
                "Hash": "xyz=",
                "State": {"$type": "GameFileSourceDownloader, Wabbajack.Lib"},
            },
        ],
    )
    m = wabbajack.load(wj)
    assert m.label == "Test List 1.2.3"
    assert [e.kind for e in m.entries] == ["mod", "game"]
    entry = m.entries[0]
    assert (entry.file_name, entry.size, entry.xxh64_b64) == ("Mod-1.7z", 123, "abc=")
    assert entry.subdir == ""


def test_placeholder_version_resolved_from_metadata_sidecar(tmp_path):
    wj = tmp_path / "old.wabbajack"
    make_wj(wj, version="0.0.1.0")
    (tmp_path / "old.wabbajack.metadata").write_text(
        json.dumps({"version": "9.9.9"}), encoding="utf-8"
    )
    assert wabbajack.load(wj).label == "Test List 9.9.9"


def test_placeholder_version_without_sidecar_kept(tmp_path):
    wj = tmp_path / "old.wabbajack"
    make_wj(wj, version="0.0.1.0")
    assert wabbajack.load(wj).label == "Test List 0.0.1.0"


def test_machine_id_from_metadata_sidecar(tmp_path):
    wj = tmp_path / "wj-featured_@@_living_skyrim.wabbajack"
    make_wj(wj, name="Living Skyrim 4", version="4.2.0.3")
    (tmp_path / wj.name).with_name(wj.name + ".metadata").write_text(
        json.dumps({"version": "4.2.0.3", "links": {"machineURL": "living_skyrim"}}),
        encoding="utf-8",
    )
    m = wabbajack.load(wj)
    assert m.machine == "living_skyrim"
    assert m.group_key == "living_skyrim"


def test_machine_id_falls_back_to_filename_convention(tmp_path):
    wj = tmp_path / "Geborgen_@@_nordic-souls.wabbajack"
    make_wj(wj, name="Nordic Souls")
    assert wabbajack.load(wj).machine == "nordic-souls"


def test_no_machine_id_groups_by_name(tmp_path):
    wj = tmp_path / "plain.wabbajack"
    make_wj(wj, name="Plain List")
    m = wabbajack.load(wj)
    assert m.machine == ""
    assert m.group_key == "plain list"


def test_bare_modlist_json(tmp_path):
    p = tmp_path / "modlist.json"
    p.write_text(
        json.dumps({"Name": "Bare", "Version": "2.0", "Archives": []}), encoding="utf-8"
    )
    assert wabbajack.load(p).label == "Bare 2.0"


def test_corrupt_metadata_sidecar_is_tolerated(tmp_path):
    wj = tmp_path / "L_@@_machine-id.wabbajack"
    make_wj(wj, name="L", version="1.0")
    (tmp_path / "L_@@_machine-id.wabbajack.metadata").write_text(
        "{not json", encoding="utf-8"
    )
    manifest = wabbajack.load(wj)
    assert manifest.label == "L 1.0"
    assert manifest.machine == "machine-id"  # filename convention still applies


def test_metadata_sidecar_with_non_dict_payload_is_tolerated(tmp_path):
    wj = tmp_path / "a.wabbajack"
    make_wj(wj, name="L", version="1.0")
    (tmp_path / "a.wabbajack.metadata").write_text('["list"]', encoding="utf-8")
    assert wabbajack.load(wj).machine == ""


def test_zip_without_modlist_raises_value_error(tmp_path):
    import pytest

    path = tmp_path / "broken.wabbajack"
    make_wj(path, entry_name="readme.txt")  # a zip with no modlist member
    with pytest.raises(ValueError, match="no modlist entry"):
        wabbajack.load(path)
