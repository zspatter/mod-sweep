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
