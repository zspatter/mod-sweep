from modsweep import sweep as sweep_mod
from modsweep.cache import HashCache
from modsweep.matcher import KEEP, UNCLAIMED, FileResult
from modsweep.scanner import scan


def make_downloads(tmp_path):
    dl = tmp_path / "downloads"
    (dl / "sub").mkdir(parents=True)
    (dl / "keep.7z").write_bytes(b"K" * 10)
    (dl / "old.7z").write_bytes(b"O" * 10)
    (dl / "old.7z.meta").write_text("[General]\n", encoding="utf-8")
    (dl / "sub" / "junk.zip").write_bytes(b"J" * 10)
    return dl


def results_for(dl):
    files = {f.rel: f for f in scan(dl)}
    return [
        FileResult(files["keep.7z"], KEEP, ["L"]),
        FileResult(files["old.7z"], UNCLAIMED),
        FileResult(files["old.7z.meta"], UNCLAIMED, sidecar=True),
        FileResult(files["sub/junk.zip"], UNCLAIMED),
    ]


def test_plan_refuses_unhashed_and_binds_sidecar(tmp_path):
    dl = make_downloads(tmp_path)
    cache = HashCache(tmp_path / "c.sqlite")
    plan = sweep_mod.plan(results_for(dl), cache)
    assert not plan.ready
    assert {r.disk.rel for r in plan.refused} == {"old.7z", "old.7z.meta", "sub/junk.zip"}


def test_plan_never_touches_keeps(tmp_path):
    dl = make_downloads(tmp_path)
    cache = HashCache(tmp_path / "c.sqlite")
    for r in results_for(dl):
        if not r.disk.is_meta:
            cache.put(r.disk, "h", 1)
    plan = sweep_mod.plan(results_for(dl), cache)
    assert "keep.7z" not in {r.disk.rel for r in plan.ready}


def test_execute_and_restore_roundtrip(tmp_path):
    dl = make_downloads(tmp_path)
    cache = HashCache(tmp_path / "c.sqlite")
    results = results_for(dl)
    for r in results:
        if not r.disk.is_meta:
            cache.put(r.disk, "h", 1)
    plan = sweep_mod.plan(results, cache)
    assert len(plan.ready) == 3 and not plan.refused

    batch = sweep_mod.execute(plan, tmp_path / "quarantine")
    assert (batch / "old.7z").exists()
    assert (batch / "sub" / "junk.zip").exists()  # structure preserved
    assert (batch / sweep_mod.MANIFEST_NAME).exists()
    assert (dl / "keep.7z").exists()
    assert not (dl / "old.7z").exists()

    moved, skipped, missing = sweep_mod.restore(batch)
    assert (moved, skipped, missing) == (3, 0, 0)
    assert (dl / "old.7z").exists()
    assert (dl / "old.7z.meta").exists()
    assert (dl / "sub" / "junk.zip").exists()


def test_list_batches_only_counts_real_batches(tmp_path):
    q = tmp_path / "quarantine"
    real = q / "2020-01-01_000000"
    real.mkdir(parents=True)
    (real / sweep_mod.MANIFEST_NAME).write_text("rel_path\n", encoding="utf-8")
    (real / "old.7z").write_bytes(b"x" * 10)
    (q / "random-folder").mkdir()  # no manifest: not a batch, never touched

    batches = sweep_mod.list_batches(q)
    assert [b.path for b in batches] == [real]
    b = batches[0]
    assert (b.created.year, b.files) == (2020, 2)
    assert b.size > 0


def test_list_batches_falls_back_to_mtime_for_odd_names(tmp_path):
    q = tmp_path / "quarantine"
    odd = q / "not-a-timestamp"
    odd.mkdir(parents=True)
    (odd / sweep_mod.MANIFEST_NAME).write_text("rel_path\n", encoding="utf-8")
    (batch,) = sweep_mod.list_batches(q)
    assert batch.created.year >= 2020  # mtime of a dir created just now


def test_purge_batch_deletes_recursively(tmp_path):
    q = tmp_path / "quarantine"
    real = q / "2020-01-01_000000"
    (real / "sub").mkdir(parents=True)
    (real / sweep_mod.MANIFEST_NAME).write_text("rel_path\n", encoding="utf-8")
    (real / "sub" / "junk.zip").write_bytes(b"x")
    (batch,) = sweep_mod.list_batches(q)
    sweep_mod.purge_batch(batch)
    assert not real.exists()
    assert q.exists()


def test_missing_quarantine_dir_lists_nothing(tmp_path):
    assert sweep_mod.list_batches(tmp_path / "nope") == []


def test_execute_tag_suffixes_batch_and_keeps_age_parseable(tmp_path):
    dl = make_downloads(tmp_path)
    cache = HashCache(tmp_path / "c.sqlite")
    results = results_for(dl)
    for r in results:
        if not r.disk.is_meta:
            cache.put(r.disk, "h", 1)
    batch = sweep_mod.execute(
        sweep_mod.plan(results, cache), tmp_path / "quarantine", tag="file"
    )
    assert batch.name.endswith("_file")
    (listed,) = sweep_mod.list_batches(tmp_path / "quarantine")
    assert listed.created.year >= 2026  # parsed from the stamp prefix, not mtime


def test_restore_refuses_to_overwrite(tmp_path):
    dl = make_downloads(tmp_path)
    cache = HashCache(tmp_path / "c.sqlite")
    results = results_for(dl)
    for r in results:
        if not r.disk.is_meta:
            cache.put(r.disk, "h", 1)
    batch = sweep_mod.execute(sweep_mod.plan(results, cache), tmp_path / "quarantine")

    (dl / "old.7z").write_bytes(b"NEW CONTENT")  # a fresh download took the spot
    moved, skipped, missing = sweep_mod.restore(batch)
    assert (moved, skipped, missing) == (2, 1, 0)
    assert (dl / "old.7z").read_bytes() == b"NEW CONTENT"
    assert (batch / "old.7z").exists()  # left in quarantine, not lost
