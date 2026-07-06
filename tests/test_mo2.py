import pytest

from modsweep import mo2


def make_mod(mods, name, ini_lines):
    d = mods / name
    d.mkdir(parents=True)
    if ini_lines is not None:
        (d / "meta.ini").write_text("\n".join(ini_lines), encoding="utf-8")


@pytest.fixture
def instance(tmp_path):
    inst = tmp_path / "SomeList"
    mods = inst / "mods"
    make_mod(mods, "[NoDelete] 00.000 QOL_separator", ["[General]"])
    make_mod(mods, "[NoDelete] 00.001 A", ["[General]", "installationFile=A-1.zip"])
    make_mod(
        mods,
        "[NoDelete] 00.002 WinPath",
        ["[General]", r"installationFile=C:\dl\B-2.7z"],
    )
    make_mod(mods, "[NoDelete] 00.003 NoArchive", ["[General]", "installationFile="])
    make_mod(mods, "Regular Mod", ["[General]", "installationFile=R-1.zip"])
    return inst


def test_nodelete_only_with_separators_skipped(instance):
    m = mo2.load(instance)
    assert m.label == "[NoDelete] SomeList"
    assert [e.file_name for e in m.entries] == ["A-1.zip", "B-2.7z"]
    assert all(e.kind == "custom" for e in m.entries)


def test_windows_path_split_works_on_any_platform(instance):
    m = mo2.load(instance)
    assert "B-2.7z" in [e.file_name for e in m.entries]


def test_include_all_covers_regular_mods(instance):
    m = mo2.load(instance, include_all=True)
    assert m.label == "MO2 install SomeList"
    assert "R-1.zip" in [e.file_name for e in m.entries]


def test_mods_dir_passed_directly(instance):
    m = mo2.load(instance / "mods")
    assert m.label == "[NoDelete] SomeList"


def test_no_mods_dir_raises(tmp_path):
    with pytest.raises(ValueError):
        mo2.load(tmp_path)
