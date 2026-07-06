from pathlib import Path

from modsweep import bundled, config
from modsweep.cli import _expand_nolvus


def test_package_dir_contains_shipped_manifest():
    pkg = bundled.package_dir()
    assert pkg is not None
    assert any(p.name.endswith(".xml.gz") for p in pkg.iterdir())


def test_user_dir_is_per_user_and_writable_location(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert bundled.user_dir() == tmp_path / "modsweep" / "manifests" / "nolvus"


def test_manifest_dirs_includes_user_dir_once_it_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled, "user_dir", lambda: tmp_path / "user")
    dirs = bundled.manifest_dirs()
    assert bundled.package_dir() in dirs
    assert tmp_path / "user" not in dirs  # not created yet

    (tmp_path / "user").mkdir()
    (tmp_path / "user" / "extra-7.0.xml.gz").write_bytes(b"")
    assert tmp_path / "user" in bundled.manifest_dirs()
    assert "extra-7.0.xml.gz" in bundled.known_names()


def test_bundled_keyword_survives_config_roundtrip(tmp_path):
    cfg = config.Config(downloads=tmp_path, nolvus=[Path("bundled")])
    target = tmp_path / "modsweep.toml"
    config.save(cfg, target)
    loaded = config.load(target)
    assert loaded.nolvus == [Path("bundled")]  # not resolved against cfg dir


def test_expand_nolvus_bundled_keyword_yields_manifest_dirs():
    sources = _expand_nolvus([Path("bundled")])
    names = {path.name for _, path, _ in sources}
    assert "nolvus-awakening-6.0.20.xml.gz" in names
    assert all(kind == "nolvus" and not pinned for kind, _, pinned in sources)