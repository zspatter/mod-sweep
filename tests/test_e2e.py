"""End-to-end lifecycle tests driving the CLI (main) against synthetic trees.

One tree exercises every source type and classification transition:
Wabbajack (exact size + xxh64, including a renamed archive and a same-size
hash contradiction), Nolvus (CRC in a category subdir), and a [NoDelete]
MO2 install (name-only claim).
"""

import csv
import sqlite3
from datetime import datetime

import pytest
from helpers import make_mo2_install, make_nolvus, make_wabbajack, wj_hash

from modsweep import sweep as sweep_mod
from modsweep.cli import main

CLAIMED = b"CLAIMED-CONTENT"
RENAMED = b"RENAMED-BLOB-CONTENT"
STALE_NEW = b"STALE-NEW!"
STALE_OLD = b"STALE-OLD!"  # same length as STALE_NEW: invisible to size checks
NOLVUS = b"NOLVUS-MOD"
JUNK = b"JUNK-DATA"


def build_tree(tmp_path):
    dl = tmp_path / "downloads"
    (dl / "1.1 CAT").mkdir(parents=True)
    (dl / "claimed.7z").write_bytes(CLAIMED)
    (dl / "claimed.7z.meta").write_text("[General]\n", encoding="utf-8")
    (dl / "renamed_blob.7z").write_bytes(RENAMED)
    (dl / "stale.7z").write_bytes(STALE_OLD)
    (dl / "junk.7z").write_bytes(JUNK)
    (dl / "junk.7z.meta").write_text("[General]\n", encoding="utf-8")
    (dl / "orphan.7z.meta").write_text("[General]\n", encoding="utf-8")
    (dl / "1.1 CAT" / "nolvus.7z").write_bytes(NOLVUS)
    (dl / "custom.7z").write_bytes(b"CUSTOM-ADDITION")

    wj = make_wabbajack(
        tmp_path / "list.wabbajack", "List", "1.0",
        [
            ("claimed.7z", len(CLAIMED), wj_hash(CLAIMED)),
            ("original-name.7z", len(RENAMED), wj_hash(RENAMED)),
            ("stale.7z", len(STALE_OLD), wj_hash(STALE_NEW)),
        ],
    )
    nolvus = make_nolvus(
        tmp_path / "InstallPackage.xml", categories={"1.1 CAT": [("nolvus.7z", NOLVUS)]}
    )
    install = make_mo2_install(tmp_path, "Inst", {"[NoDelete] 00.001 C": "custom.7z"})

    args = [
        "--downloads", str(dl),
        "-m", str(wj),
        "-m", str(nolvus),
        "-m", str(install),
        "--cache", str(tmp_path / "cache.sqlite"),
    ]
    return dl, args


def statuses(tmp_path, args) -> dict[str, str]:
    out = tmp_path / "report.csv"
    assert main(["report", *args, "--csv", str(out)]) == 0
    with open(out, encoding="utf-8-sig") as fh:
        return {row["rel_path"]: row["status"] for row in csv.DictReader(fh)}


def test_full_lifecycle(tmp_path, capsys):
    dl, args = build_tree(tmp_path)
    quarantine = tmp_path / "quarantine"

    # 1. Pre-hash: name/size classification only.
    s = statuses(tmp_path, args)
    assert s["claimed.7z"] == "keep"
    assert s["claimed.7z.meta"] == "keep"  # sidecar follows its archive
    assert s["stale.7z"] == "keep"  # same size: undetectable without hashes
    assert s["renamed_blob.7z"] == "unclaimed"
    assert s["junk.7z"] == "unclaimed"
    assert s["orphan.7z.meta"] == "meta-orphan"
    assert s["1.1 CAT/nolvus.7z"] == "keep"
    assert s["custom.7z"] == "keep"

    # 2. The hash gate refuses unhashed archives (orphan .meta needs no hash).
    capsys.readouterr()
    assert main(["sweep", *args, "--quarantine", str(quarantine)]) == 0
    text = capsys.readouterr().out
    assert "Refused (hash never checked): 3 files" in text  # junk + its meta + renamed
    assert "Sweep plan: 1 files" in text  # only the orphan .meta is sweepable

    # 3. Hashing flips classifications to their true values.
    assert main(["hash", *args]) == 0
    s = statuses(tmp_path, args)
    assert s["claimed.7z"] == "keep-verified"
    assert s["renamed_blob.7z"] == "keep-verified"  # hash rescue, name unknown
    assert s["stale.7z"] == "stale-version"  # same size, hash contradiction
    assert s["1.1 CAT/nolvus.7z"] == "keep-verified"  # via CRC32
    assert s["custom.7z"] == "keep"  # [NoDelete] name-only claim
    assert s["junk.7z"] == "unclaimed"

    # 4. Apply the sweep: candidates and their sidecars leave, keeps stay.
    assert main(["sweep", *args, "--quarantine", str(quarantine), "--apply"]) == 0
    for gone in ("stale.7z", "junk.7z", "junk.7z.meta", "orphan.7z.meta"):
        assert not (dl / gone).exists()
    for kept in ("claimed.7z", "claimed.7z.meta", "renamed_blob.7z", "custom.7z"):
        assert (dl / kept).exists()
    assert (dl / "1.1 CAT" / "nolvus.7z").exists()

    # 5. Post-sweep the tree is exactly its whitelist.
    s = statuses(tmp_path, args)
    assert all(v in ("keep", "keep-verified") for v in s.values())

    # 6. Restore round-trips the batch.
    (batch,) = sweep_mod.list_batches(quarantine)
    assert main(["restore", str(batch.path)]) == 0
    assert (dl / "junk.7z").exists()
    assert (dl / "stale.7z").exists()


def test_purge_lifecycle(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)  # hermetic: no repo modsweep.toml
    quarantine = tmp_path / "quarantine"
    old = quarantine / "2020-01-01_000000"
    old.mkdir(parents=True)
    (old / sweep_mod.MANIFEST_NAME).write_text("rel_path\n", encoding="utf-8")
    (old / "old.7z").write_bytes(b"x")
    fresh = quarantine / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    fresh.mkdir()
    (fresh / sweep_mod.MANIFEST_NAME).write_text("rel_path\n", encoding="utf-8")

    assert main(["purge", "--quarantine", str(quarantine), "--older-than", "30"]) == 0
    text = capsys.readouterr().out
    assert "[purge]" in text and "[keep]" in text and "Dry run" in text
    assert old.exists() and fresh.exists()

    assert main(
        ["purge", "--quarantine", str(quarantine), "--older-than", "30", "--apply"]
    ) == 0
    assert not old.exists()
    assert fresh.exists()


def test_modified_file_requires_rehash_before_sweep(tmp_path, capsys):
    """The hash gate re-applies when a hashed file changes on disk: the
    cache invalidates on size/mtime, so the file counts as unhashed again."""
    dl, args = build_tree(tmp_path)
    assert main(["hash", *args]) == 0

    modified = dl / "junk.7z"
    modified.write_bytes(b"DIFFERENT CONTENT NOW")  # size and mtime change

    capsys.readouterr()
    assert main(["sweep", *args, "--quarantine", str(tmp_path / "q")]) == 0
    text = capsys.readouterr().out
    assert "Refused (hash never checked): 2 files" in text  # junk.7z + its .meta
    assert modified.exists()

    assert main(["hash", *args]) == 0  # re-hash picks it up again
    capsys.readouterr()
    assert main(["sweep", *args, "--quarantine", str(tmp_path / "q")]) == 0
    assert "Refused" not in capsys.readouterr().out


def test_log_level_debug_emits_diagnostics(tmp_path, capsys):
    _, args = build_tree(tmp_path)
    assert main(["report", *args, "--log-level", "debug"]) == 0
    err = capsys.readouterr().err
    assert "resolved 3 active source(s)" in err
    assert "scanned" in err and "matched" in err
    assert "loaded List 1.0 (wabbajack)" in err

    # The second run parses nothing: the manifest cache serves it.
    assert main(["report", *args, "--log-level", "debug"]) == 0
    err = capsys.readouterr().err
    assert "manifest cache hit" in err
    assert "loaded List 1.0 (wabbajack)" not in err

    capsys.readouterr()
    assert main(["report", *args]) == 0  # default: diagnostics stay quiet
    assert "scanned" not in capsys.readouterr().err


def test_quarantine_inside_downloads_rejected(tmp_path):
    dl, args = build_tree(tmp_path)
    with pytest.raises(SystemExit):
        main(["sweep", *args, "--quarantine", str(dl / "q")])


def test_missing_downloads_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no config anywhere
    with pytest.raises(SystemExit):
        main(["report", "-m", "x.wabbajack"])


def test_purge_without_quarantine_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no config: nothing supplies a quarantine dir
    with pytest.raises(SystemExit, match="no --quarantine"):
        main(["purge"])


def cached_rows(tmp_path) -> int:
    with sqlite3.connect(tmp_path / "cache.sqlite") as conn:
        (count,) = conn.execute("SELECT COUNT(*) FROM hashes").fetchone()
    conn.close()
    return count


def test_hash_limit_caps_work(tmp_path):
    _, args = build_tree(tmp_path)
    assert main(["hash", *args, "--limit", "2"]) == 0
    assert cached_rows(tmp_path) == 2


def test_hash_only_candidates_hashes_just_candidates(tmp_path):
    _, args = build_tree(tmp_path)
    assert main(["hash", *args, "--only-candidates"]) == 0
    # junk.7z + renamed_blob.7z: the pre-hash candidates, excluding sidecars
    assert cached_rows(tmp_path) == 2


def test_snapshot_keeps_protection_when_manifest_vanishes(tmp_path, monkeypatch, capsys):
    """The snapshot story end to end: with a snapshot registered, deleting
    the .wabbajack costs nothing - same label stays active, so no drift
    warning fires and the files remain protected."""
    monkeypatch.chdir(tmp_path)
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "claimed.7z").write_bytes(CLAIMED)
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    make_wabbajack(
        wj_dir / "list.wabbajack", "List", "1.0",
        [("claimed.7z", len(CLAIMED), wj_hash(CLAIMED))],
    )
    snaps = tmp_path / "snaps"
    cfg = tmp_path / "cfg.toml"

    def write_config(snapshot_line: str) -> None:
        cfg.write_text(
            f"downloads = '{dl}'\ncache = '{tmp_path / '.modsweep' / 'h.sqlite'}'\n"
            f"wabbajack = ['{wj_dir}']\n{snapshot_line}\n",
            encoding="utf-8",
        )

    write_config("")
    assert main(["snapshot", "--config", str(cfg), "--out", str(snaps)]) == 0
    (snapshot_file,) = snaps.glob("*.json")
    write_config(f"snapshots = ['{snapshot_file}']")
    assert main(["report", "--config", str(cfg)]) == 0  # baseline with both
    capsys.readouterr()

    (wj_dir / "list.wabbajack").unlink()
    out_csv = tmp_path / "r.csv"
    assert main(["report", "--config", str(cfg), "--csv", str(out_csv)]) == 0
    captured = capsys.readouterr()
    assert "vanished" not in captured.err  # the label never went inactive
    assert "List 1.0" in captured.out  # still an active source (via snapshot)
    with open(out_csv, encoding="utf-8-sig") as fh:
        (row,) = list(csv.DictReader(fh))
    assert row["status"] == "keep"  # protected without the .wabbajack


def test_sweep_restore_sweep_is_idempotent(tmp_path):
    dl, args = build_tree(tmp_path)
    quarantine = tmp_path / "quarantine"
    assert main(["hash", *args]) == 0

    assert main(["sweep", *args, "--quarantine", str(quarantine), "--apply"]) == 0
    first_gone = {p.name for p in dl.rglob("*") if p.is_file()}
    (batch,) = sweep_mod.list_batches(quarantine)
    assert main(["restore", str(batch.path)]) == 0
    assert sweep_mod.list_batches(quarantine) == []  # fully restored, no husk

    assert main(["sweep", *args, "--quarantine", str(quarantine), "--apply"]) == 0
    second_gone = {p.name for p in dl.rglob("*") if p.is_file()}
    assert second_gone == first_gone  # identical outcome on the rerun
    (batch,) = sweep_mod.list_batches(quarantine)
    assert batch.files > 1  # the re-swept candidates plus the manifest


def test_snapshots_classify_identically_to_sources(tmp_path):
    dl, args = build_tree(tmp_path)
    out = tmp_path / "snaps"
    assert main(["snapshot", *args, "--out", str(out)]) == 0
    snaps = sorted(out.glob("*.json"))
    assert len(snaps) == 3
    snap_args = ["--downloads", str(dl), "--cache", str(tmp_path / "cache.sqlite")]
    for s in snaps:
        snap_args += ["-m", str(s)]
    assert statuses(tmp_path, snap_args) == statuses(tmp_path, args)


# --- config-driven resolution flows ------------------------------------------


def write_config(tmp_path, dl, extra="") -> list[str]:
    """A config file naming the same tree build_tree makes via -m args."""
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"""
downloads = '{dl}'
cache = '{tmp_path / "cache.sqlite"}'
wabbajack = ['{tmp_path / "list.wabbajack"}']
nolvus = ['{tmp_path / "InstallPackage.xml"}']
installs = ['{tmp_path / "Inst"}']
{extra}
""",
        encoding="utf-8",
    )
    return ["--config", str(cfg)]


def test_exclude_by_label_retires_list_and_announces(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    args = write_config(tmp_path, dl, extra="exclude = ['List 1.0']\n")
    assert main(["report", *args]) == 0
    captured = capsys.readouterr()
    assert "excluded (List 1.0): List 1.0" in captured.err
    # The wabbajack list is retired: its unique files read as candidates.
    assert "List 1.0" not in captured.out
    assert "claimed.7z" in captured.out  # now a deletion candidate


def test_exclude_by_file_name_skips_before_parsing(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    args = write_config(tmp_path, dl)
    assert main(["report", *args, "--exclude", "list.wabbajack"]) == 0
    err = capsys.readouterr().err
    assert "excluded (list.wabbajack): list.wabbajack" in err


def test_cli_exclude_extends_config_excludes(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    args = write_config(tmp_path, dl, extra="exclude = ['List*']\n")
    assert main(["report", *args, "--exclude", "Guide*"]) == 0
    err = capsys.readouterr().err
    assert "excluded (List*)" in err
    assert "excluded (Guide*): Guide 1.0" in err


def test_latest_only_supersedes_and_pin_rescues_through_main(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    (tmp_path / "old").mkdir()
    old = make_wabbajack(tmp_path / "old" / "list.wabbajack", "List", "0.9", [])
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"""
downloads = '{dl}'
cache = '{tmp_path / "cache.sqlite"}'
latest_only = true
wabbajack = ['{tmp_path}']
""",
        encoding="utf-8",
    )
    assert main(["report", "--config", str(cfg)]) == 0
    captured = capsys.readouterr()
    assert "superseded by List 1.0: List 0.9" in captured.err

    # Naming the old file directly pins it through the filter.
    cfg.write_text(
        f"""
downloads = '{dl}'
cache = '{tmp_path / "cache.sqlite"}'
latest_only = true
wabbajack = ['{tmp_path}', '{old}']
""",
        encoding="utf-8",
    )
    assert main(["report", "--config", str(cfg)]) == 0
    captured = capsys.readouterr()
    assert "pinned (explicit entry) despite List 1.0: List 0.9" in captured.err
    assert "List 0.9" in captured.out  # active alongside the winner


def test_empty_install_contributes_nothing(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    make_mo2_install(tmp_path, "EmptyInst", {"plain mod": "whatever.7z"})
    write_config(tmp_path, dl)
    cfg = tmp_path / "modsweep.toml"
    inst = tmp_path / "Inst"
    empty = tmp_path / "EmptyInst"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            f"installs = ['{inst}']",
            f"installs = ['{inst}', '{empty}']",
        ),
        encoding="utf-8",
    )
    assert main(["report", "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "[NoDelete] Inst" in out
    assert "EmptyInst" not in out  # no [NoDelete] mods: dropped from evaluation


# --- CLI edges and error paths ------------------------------------------------


def test_report_with_no_resolvable_sources_exits_1(tmp_path, capsys):
    dl = tmp_path / "downloads"
    dl.mkdir()
    make_mo2_install(tmp_path, "EmptyInst", {"plain mod": "whatever.7z"})
    cfg = tmp_path / "modsweep.toml"
    cache = tmp_path / "c.sqlite"
    empty = tmp_path / "EmptyInst"
    cfg.write_text(
        f"downloads = '{dl}'\ncache = '{cache}'\ninstalls = ['{empty}']\n",
        encoding="utf-8",
    )
    assert main(["report", "--config", str(cfg)]) == 1
    assert "No manifests found." in capsys.readouterr().err
    assert main(["sweep", "--config", str(cfg)]) == 1
    assert main(["snapshot", "--config", str(cfg)]) == 1


def test_unrecognized_manifest_type_warns_then_errors(tmp_path, capsys):
    dl = tmp_path / "downloads"
    dl.mkdir()
    stray = tmp_path / "notes.txt"
    stray.write_text("not a manifest", encoding="utf-8")
    with pytest.raises(SystemExit, match="no manifest sources"):
        main(["report", "--downloads", str(dl), "-m", str(stray)])
    assert "unrecognized manifest type" in capsys.readouterr().err


def test_installs_entry_without_mo2_layout_warns(tmp_path, capsys):
    dl, _ = build_tree(tmp_path)
    plain = tmp_path / "plain-folder"
    plain.mkdir()
    write_config(tmp_path, dl)
    cfg = tmp_path / "modsweep.toml"
    inst = tmp_path / "Inst"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace(
            f"installs = ['{inst}']",
            f"installs = ['{plain}']",
        ),
        encoding="utf-8",
    )
    assert main(["report", "--config", str(cfg)]) == 0
    assert "no MO2 install (mods/) found" in capsys.readouterr().err


def test_dash_m_directory_of_installs_expands_each(tmp_path, capsys):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "a.7z").write_bytes(b"A")
    (dl / "b.7z").write_bytes(b"B")
    parent = tmp_path / "installs"
    make_mo2_install(parent, "One", {"[NoDelete] 1 a": "a.7z"})
    make_mo2_install(parent, "Two", {"[NoDelete] 2 b": "b.7z"})
    assert main([
        "report", "--downloads", str(dl), "-m", str(parent),
        "--cache", str(tmp_path / "c.sqlite"),
    ]) == 0
    out = capsys.readouterr().out
    assert "[NoDelete] One" in out and "[NoDelete] Two" in out


def test_restore_reports_skipped_and_missing(tmp_path, capsys):
    dl, args = build_tree(tmp_path)
    quarantine = tmp_path / "q"
    assert main(["hash", *args]) == 0
    assert main(["sweep", *args, "--quarantine", str(quarantine), "--apply"]) == 0
    (batch,) = sweep_mod.list_batches(quarantine)

    (dl / "junk.7z").write_bytes(b"NEW OCCUPANT")  # occupies an original path
    (batch.path / "stale.7z").unlink()  # vanished from the batch

    capsys.readouterr()
    assert main(["restore", str(batch.path)]) == 0
    out = capsys.readouterr().out
    assert "1 skipped: original path already occupied" in out
    assert "1 listed in the manifest were not found in the batch" in out


def test_sweep_plan_truncates_after_ten_largest(tmp_path, capsys):
    dl = tmp_path / "downloads"
    dl.mkdir()
    for i in range(12):
        (dl / f"junk{i:02}.7z").write_bytes(bytes([i]) * (i + 1))
    wj = make_wabbajack(tmp_path / "l.wabbajack", "L", "1.0", [])
    args = [
        "--downloads", str(dl), "-m", str(wj),
        "--cache", str(tmp_path / "c.sqlite"),
    ]
    assert main(["hash", *args]) == 0
    capsys.readouterr()
    assert main(["sweep", *args, "--quarantine", str(tmp_path / "q")]) == 0
    assert "... and 2 more archives (+ sidecars)" in capsys.readouterr().out


def test_sweep_defaults_quarantine_next_to_downloads(tmp_path, capsys):
    _, args = build_tree(tmp_path)
    capsys.readouterr()
    assert main(["sweep", *args]) == 0  # dry run: no --quarantine anywhere
    assert str(tmp_path / "_quarantine") in capsys.readouterr().out


def test_hash_interrupt_reports_resumable(tmp_path, capsys, monkeypatch):
    from modsweep import cli as cli_mod

    _, args = build_tree(tmp_path)

    def boom(pending, total_bytes, cache):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_mod, "_hash_files", boom)
    assert main(["hash", *args]) == 0
    assert "progress is cached; rerun to resume" in capsys.readouterr().out


def test_purge_with_empty_quarantine_reports_none(tmp_path, capsys):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    assert main(["purge", "--quarantine", str(quarantine)]) == 0
    assert "No sweep batches under" in capsys.readouterr().out


def test_check_update_malformed_response_exits_cleanly(monkeypatch):
    import urllib.request

    class FakeResponse:
        def read(self):
            return b"<html>rate limited</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout: FakeResponse()
    )
    with pytest.raises(SystemExit, match="update check failed"):
        main(["check-update"])
