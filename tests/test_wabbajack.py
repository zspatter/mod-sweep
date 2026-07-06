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


def test_bare_modlist_json(tmp_path):
    p = tmp_path / "modlist.json"
    p.write_text(
        json.dumps({"Name": "Bare", "Version": "2.0", "Archives": []}), encoding="utf-8"
    )
    assert wabbajack.load(p).label == "Bare 2.0"
