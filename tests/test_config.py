import pytest

from modsweep import config


def test_load_resolves_relative_against_config_dir(tmp_path):
    downloads = tmp_path / "dl"
    quarantine = tmp_path / "q"
    p = tmp_path / "modsweep.toml"
    p.write_text(
        f"""
downloads = '{downloads}'
wabbajack = ['wj']
nolvus = ['manifests/InstallPackage.xml']
exclude = ['LoreRim 2.2*']
latest_only = true

[quarantine]
dir = '{quarantine}'
""",
        encoding="utf-8",
    )
    cfg = config.load(p)
    assert cfg.downloads == downloads  # absolute: taken as-is
    assert cfg.wabbajack == [tmp_path / "wj"]  # relative: anchored to config dir
    assert cfg.nolvus == [tmp_path / "manifests" / "InstallPackage.xml"]
    assert cfg.exclude == ["LoreRim 2.2*"]
    assert cfg.latest_only is True
    assert cfg.quarantine == quarantine
    assert cfg.has_sources


def test_missing_default_config_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config.load(None)
    assert cfg.downloads is None
    assert not cfg.has_sources
    assert cfg.latest_only is False


def test_explicit_missing_config_errors(tmp_path):
    with pytest.raises(SystemExit):
        config.load(tmp_path / "nope.toml")
