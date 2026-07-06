import pytest

from modsweep import config

TOML = """
downloads = 'X:\\dl'
wabbajack = ['wj']
nolvus = ['manifests/InstallPackage.xml']
installs = ['X:\\installs']
exclude = ['LoreRim 2.2*']

[quarantine]
dir = 'X:\\quarantine'
"""


def test_load_resolves_relative_against_config_dir(tmp_path):
    p = tmp_path / "modsweep.toml"
    p.write_text(TOML, encoding="utf-8")
    cfg = config.load(p)
    assert cfg.downloads is not None and str(cfg.downloads) == "X:\\dl"
    assert cfg.wabbajack == [tmp_path / "wj"]
    assert cfg.nolvus == [tmp_path / "manifests" / "InstallPackage.xml"]
    assert cfg.exclude == ["LoreRim 2.2*"]
    assert cfg.quarantine is not None and str(cfg.quarantine) == "X:\\quarantine"
    assert cfg.has_sources


def test_missing_default_config_is_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = config.load(None)
    assert cfg.downloads is None
    assert not cfg.has_sources


def test_explicit_missing_config_errors(tmp_path):
    with pytest.raises(SystemExit):
        config.load(tmp_path / "nope.toml")
