from pathlib import Path

from modsweep.manifest import Manifest, latest_only, version_key


def man(name, version):
    return Manifest(
        label=f"{name} {version}".strip(),
        source_path=Path("m"),
        name=name,
        version=version,
    )


def test_version_key_numeric_ordering():
    assert version_key("10.0") > version_key("9.9")
    assert version_key("4.3.0.5") > version_key("2.2.11")
    assert version_key("13.5.3") > version_key("13.3.2")


def test_version_key_suffixes_and_empty():
    assert version_key("1.2.3b") > version_key("1.2.3")
    assert version_key("") < version_key("0.0.1")  # empty sorts lowest
    assert version_key("beta") > version_key("1")  # pure text sorts after numeric


def test_latest_only_keeps_newest_per_name():
    old, new, other = man("LoreRim", "2.2.11"), man("LoreRim", "4.3.0.5"), man("NGVO", "2.0.0")
    kept, superseded, pinned_kept = latest_only([old, new, other])
    assert kept == [new, other]
    assert superseded == [(old, new)]
    assert pinned_kept == []


def test_latest_only_order_independent():
    old, new = man("LoreRim", "2.2.11"), man("LoreRim", "4.3.0.5")
    kept, _, _ = latest_only([new, old])
    assert kept == [new]


def test_superseded_names_final_winner():
    v1, v2, v3 = man("X", "1.0"), man("X", "2.0"), man("X", "3.0")
    kept, superseded, _ = latest_only([v1, v2, v3])
    assert kept == [v3]
    assert superseded == [(v1, v3), (v2, v3)]


def test_versionless_sources_form_single_groups():
    nd1 = man("[NoDelete] Licentia Next", "")
    nd2 = man("[NoDelete] LoreRim", "")
    lst = man("LoreRim", "4.3.0.5")
    kept, superseded, _ = latest_only([nd1, nd2, lst])
    assert kept == [nd1, nd2, lst]
    assert superseded == []


def test_grouping_is_case_insensitive():
    a, b = man("lorerim", "1.0"), man("LoreRim", "2.0")
    kept, _, _ = latest_only([a, b])
    assert kept == [b]


def test_group_key_tiers_machine_over_name_over_label():
    assert Manifest(
        label="L 1.0", source_path=Path("m"), name="Name", machine="Machine_ID"
    ).group_key == "machine_id"
    assert Manifest(
        label="L 1.0", source_path=Path("m"), name="Name"
    ).group_key == "name"
    assert Manifest(label="L 1.0", source_path=Path("m")).group_key == "l 1.0"


def test_same_name_different_machine_stay_separate():
    """Two genuinely different lists sharing a display name never collapse:
    the machine id outranks the name."""
    a = Manifest(
        label="Skyrim Redux 1.0", source_path=Path("m"),
        name="Skyrim Redux", version="1.0", machine="redux_by_alice",
    )
    b = Manifest(
        label="Skyrim Redux 2.0", source_path=Path("m"),
        name="Skyrim Redux", version="2.0", machine="redux_by_bob",
    )
    kept, superseded, _ = latest_only([a, b])
    assert kept == [a, b]  # separate groups, both survive
    assert superseded == []


def test_mixed_metadata_availability_may_split_groups():
    """Documented behavior: a manifest without a machine id falls back to
    its name, so it only groups with machine-tagged versions when the
    machine id happens to equal the lowercased name."""
    tagged = Manifest(
        label="Living Skyrim 4 4.0", source_path=Path("m"),
        name="Living Skyrim 4", version="4.0", machine="living_skyrim",
    )
    untagged_matching = Manifest(  # name coincides with the machine id
        label="living skyrim 3.0", source_path=Path("m"),
        name="living_skyrim", version="3.0",
    )
    kept, _, _ = latest_only([tagged, untagged_matching])
    assert kept == [tagged]  # coincidental key match: grouped

    untagged_differing = Manifest(  # display name differs from machine id
        label="Living Skyrim 3.0", source_path=Path("m"),
        name="Living Skyrim", version="3.0",
    )
    kept, superseded, _ = latest_only([tagged, untagged_differing])
    assert kept == [tagged, untagged_differing]  # keys differ: separate groups
    assert superseded == []


def test_machine_id_groups_renamed_lists():
    old = Manifest(
        label="Living Skyrim 3.0", source_path=Path("m"),
        name="Living Skyrim", version="3.0", machine="living_skyrim",
    )
    new = Manifest(
        label="Living Skyrim 4 4.0", source_path=Path("m"),
        name="Living Skyrim 4", version="4.0", machine="living_skyrim",
    )
    kept, superseded, _ = latest_only([old, new])
    assert kept == [new]  # renamed between releases, still one list
    assert superseded == [(old, new)]


# --- pinning (explicit entries survive the filter) ------------------------


def test_pinned_old_version_survives_alongside_winner():
    old, new = man("LoreRim", "2.2.11"), man("LoreRim", "4.3.0.5")
    kept, superseded, pinned_kept = latest_only([old, new], pinned={old.label})
    assert kept == [old, new]
    assert superseded == []
    assert pinned_kept == [(old, new)]


def test_pinning_the_winner_does_not_resurrect_older():
    old, new = man("LoreRim", "2.2.11"), man("LoreRim", "4.3.0.5")
    kept, superseded, pinned_kept = latest_only([old, new], pinned={new.label})
    assert kept == [new]
    assert superseded == [(old, new)]
    assert pinned_kept == []
