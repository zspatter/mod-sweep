import argparse
import json
import zipfile
import zlib

import pytest

from modsweep import sweep as sweep_mod
from modsweep.cli import (
    _build_parser,
    _expand_nolvus,
    _expand_wabbajack,
    _infer_file_kind,
    _purge_threshold,
    _resolve,
    exact_exclude_pattern,
    is_exact_exclude,
    load_manifests,
    main,
    survey_sources,
)
from modsweep.config import Config


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


def test_expand_nolvus_dir_is_implicit_file_is_pinned(tmp_path):
    bundled = tmp_path / "manifests"
    bundled.mkdir()
    (bundled / "nolvus-5.0.xml.gz").write_bytes(b"")
    (bundled / "nolvus-6.0.xml").write_text("", encoding="utf-8")
    (bundled / "readme.txt").write_text("", encoding="utf-8")  # ignored
    own = tmp_path / "InstallPackage.xml"

    sources = _expand_nolvus([bundled, own])
    assert ("nolvus", bundled / "nolvus-5.0.xml.gz", False) in sources
    assert ("nolvus", bundled / "nolvus-6.0.xml", False) in sources
    assert ("nolvus", own, True) in sources
    assert len(sources) == 3


def test_infer_file_kind_handles_gz(tmp_path):
    assert _infer_file_kind(tmp_path / "x.xml.gz") == "nolvus"


def test_expand_installs_finds_nested_nolvus_instances(tmp_path):
    from modsweep.cli import _expand_installs

    (tmp_path / "LoreRim" / "mods").mkdir(parents=True)  # flat child install
    nested = tmp_path / "Nolvus" / "Instances" / "Nolvus Awakening"
    (nested / "MODS" / "mods").mkdir(parents=True)  # detected via MODS child
    too_deep = tmp_path / "a" / "b" / "c" / "d"
    (too_deep / "mods").mkdir(parents=True)  # beyond the depth bound

    sources = _expand_installs([tmp_path], "mo2")
    paths = {p for _, p, _ in sources}
    assert tmp_path / "LoreRim" in paths
    assert nested in paths
    assert too_deep not in paths  # bounded: not walked arbitrarily deep
    assert all(not pinned for _, _, pinned in sources)  # dir walk = implicit


# --- resolution plumbing ---------------------------------------------------


def test_resolve_precedence_cli_over_config(tmp_path):
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"""
downloads = '{tmp_path / "cfg_dl"}'
cache = '{tmp_path / "cfg_cache.sqlite"}'
wabbajack = ['some.wabbajack']
exclude = ['A*']
latest_only = false
""",
        encoding="utf-8",
    )
    args = _build_parser().parse_args(
        [
            "report", "--config", str(cfg),
            "--downloads", str(tmp_path / "cli_dl"),
            "--exclude", "B*",
            "--latest-only",
        ]
    )
    res = _resolve(args)
    assert res.downloads == tmp_path / "cli_dl"  # CLI overrides config
    assert res.cache == tmp_path / "cfg_cache.sqlite"  # config fills the gap
    assert res.exclude == ["A*", "B*"]  # exclude is additive
    assert res.latest_only is True  # CLI flag ORs with config
    assert res.from_config is True  # no -m given: config sources


def test_resolve_cli_sources_replace_config_entirely(tmp_path):
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"downloads = '{tmp_path}'\nwabbajack = ['config.wabbajack']\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "cli.wabbajack"
    make_wj(explicit, "X", "1.0")
    args = _build_parser().parse_args(
        ["report", "--config", str(cfg), "-m", str(explicit)]
    )
    res = _resolve(args)
    assert res.sources == [("wabbajack", explicit, True)]
    assert res.from_config is False


def test_infer_file_kind(tmp_path):
    assert _infer_file_kind(tmp_path / "x.wabbajack") == "wabbajack"
    assert _infer_file_kind(tmp_path / "x.xml") == "nolvus"
    assert _infer_file_kind(tmp_path / "x.7z") is None

    modlist = tmp_path / "modlist.json"
    modlist.write_text(json.dumps({"Name": "X", "Archives": []}), encoding="utf-8")
    assert _infer_file_kind(modlist) == "wabbajack"

    snap = tmp_path / "snap.json"
    snap.write_text(
        json.dumps({"modsweep_snapshot": 1, "label": "X", "entries": []}),
        encoding="utf-8",
    )
    assert _infer_file_kind(snap) == "snapshot"


def test_purge_threshold_precedence():
    explicit = argparse.Namespace(older_than=5)
    unset = argparse.Namespace(older_than=None)
    assert _purge_threshold(explicit, Config(quarantine_keep_days=10)) == 5
    assert _purge_threshold(unset, Config(quarantine_keep_days=10)) == 10
    assert _purge_threshold(unset, Config()) == 30


# --- survey (nothing dropped, states tagged) --------------------------------


def survey_fixture(tmp_path):
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    make_wj(wj_dir / "old.wabbajack", "X", "1.0")
    make_wj(wj_dir / "new.wabbajack", "X", "2.0")
    make_wj(wj_dir / "other.wabbajack", "Other", "1.0")
    return _expand_wabbajack([wj_dir])


def test_survey_tags_active_excluded_and_superseded(tmp_path):
    sources = survey_fixture(tmp_path)
    infos = {
        i.manifest.label: i
        for i in survey_sources(sources, exclude=["Other*"], latest_only=True)
    }
    assert len(infos) == 3  # nothing dropped
    assert infos["X 2.0"].state == "active"
    assert (infos["X 1.0"].state, infos["X 1.0"].detail) == ("superseded", "X 2.0")
    assert (infos["Other 1.0"].state, infos["Other 1.0"].detail) == ("excluded", "Other*")


def test_survey_tags_pinned(tmp_path):
    wj_dir = tmp_path / "lists"
    sources = survey_fixture(tmp_path) + _expand_wabbajack([wj_dir / "old.wabbajack"])
    infos = {i.manifest.label: i.state for i in survey_sources(sources, latest_only=True)}
    assert infos["X 1.0"] == "pinned"
    assert infos["X 2.0"] == "active"


def test_exact_exclude_pattern_survives_brackets():
    label = "[NoDelete] Licentia Next"
    pattern = exact_exclude_pattern(label)
    assert is_exact_exclude(pattern, label)
    assert is_exact_exclude(label.upper(), label)  # raw label, case-insensitive
    assert not is_exact_exclude("LoreRim*", "LoreRim 2.2.11")  # glob is not exact
    # the escaped pattern actually matches through the exclusion machinery
    from modsweep.cli import _excluded_by

    assert _excluded_by(label, [pattern]) == pattern


# --- update commands ---------------------------------------------------------


def test_update_manifests_command_reports_downloads(monkeypatch, capsys):
    from modsweep import remote

    monkeypatch.setattr(remote, "update_manifests", lambda: ["nolvus-7.0.xml.gz"])
    assert main(["update-manifests"]) == 0
    out = capsys.readouterr().out
    assert "downloaded nolvus-7.0.xml.gz" in out
    assert "'bundled' config entry picks them up" in out

    monkeypatch.setattr(remote, "update_manifests", lambda: [])
    assert main(["update-manifests"]) == 0
    assert "up to date" in capsys.readouterr().out


def test_update_manifests_command_wraps_network_errors(monkeypatch):
    from modsweep import remote

    def boom():
        raise OSError("no route to host")

    monkeypatch.setattr(remote, "update_manifests", boom)
    with pytest.raises(SystemExit, match="manifest update failed"):
        main(["update-manifests"])


def test_check_update_command(monkeypatch, capsys):
    from modsweep import remote

    monkeypatch.setattr(
        remote, "check_update",
        lambda current: remote.UpdateInfo(current, "9.9.9", "https://example/rel"),
    )
    assert main(["check-update"]) == 0
    out = capsys.readouterr().out
    assert "Update available: v9.9.9" in out
    assert "https://example/rel" in out

    monkeypatch.setattr(remote, "check_update", lambda current: None)
    assert main(["check-update"]) == 0
    assert "up to date" in capsys.readouterr().out


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


def test_duplicate_label_with_differing_contents_warns(tmp_path, capsys):
    from helpers import make_mo2_install

    a = make_mo2_install(tmp_path / "a", "Inst", {"[NoDelete] 1 x": "one.7z"})
    b = make_mo2_install(tmp_path / "b", "Inst", {"[NoDelete] 2 y": "two.7z"})
    manifests = load_manifests([("mo2", a, True), ("mo2", b, True)])
    assert len(manifests) == 1  # first copy wins, as documented
    err = capsys.readouterr().err
    assert "duplicate label [NoDelete] Inst" in err
    assert "contents differ" in err


def test_duplicate_label_identical_copies_dedupe_silently(tmp_path, capsys):
    one = tmp_path / "one.wabbajack"
    two = tmp_path / "two" / "one.wabbajack"
    two.parent.mkdir()
    make_wj(one, "List", "1.0")
    make_wj(two, "List", "1.0")
    manifests = load_manifests([("wabbajack", one, False), ("wabbajack", two, False)])
    assert len(manifests) == 1
    assert "duplicate label" not in capsys.readouterr().err
