"""modsweep command-line interface.

`report` and `hash` are read-only against the downloads directory. `sweep`
never hard-deletes on its own: candidates move to a quarantine batch that
`restore` can put back (`--delete` composes sweep with an immediate purge).
Defaults come from modsweep.toml (see config.py); CLI arguments override it.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import (
    config,
    manifest_cache,
    mo2,
    nolvus,
    snapshot as snapshot_mod,
    state,
    sweep as sweep_mod,
    wabbajack,
)
from .cache import HashCache
from .manifest import Manifest
from .matcher import match
from .report import summarize, write_csv
from .scanner import DiskFile, scan

DEFAULT_CACHE = Path(".modsweep") / "hashes.sqlite"

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, getattr(args, "log_level", "warning").upper()),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    handlers = {
        "report": _cmd_report,
        "hash": _cmd_hash,
        "sweep": _cmd_sweep,
        "restore": _cmd_restore,
        "snapshot": _cmd_snapshot,
        "purge": _cmd_purge,
    }
    return handlers[args.cmd](args)


# --- argument parsing -------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modsweep",
        description="Whitelist-driven cleanup for shared modlist archive directories.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    common = _common_options()
    _add_report(sub, common)
    _add_hash(sub, common)
    _add_sweep(sub, common)
    _add_restore(sub)
    _add_snapshot(sub, common)
    _add_purge(sub)
    return parser


def _common_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config", type=Path,
        help=f"Config file (default: ./{config.DEFAULT_NAME} if present)",
    )
    common.add_argument("--downloads", type=Path, help="Archive directory root")
    common.add_argument(
        "-m", "--manifest", dest="manifests", action="append", type=Path,
        help=".wabbajack / modlist.json / InstallPackage.xml, an MO2 install "
        "([NoDelete] additions), or a directory of either (repeatable; "
        "overrides config manifests)",
    )
    common.add_argument(
        "--mo2-all", dest="mo2_all", action="append", type=Path,
        help="MO2 install whitelisted by archive NAME for its entire mod list "
        "(recovery source when a .wabbajack manifest is gone)",
    )
    common.add_argument("--cache", type=Path, help="Hash cache path")
    common.add_argument(
        "--exclude", action="append", default=[], metavar="GLOB",
        help="Retire a list: case-insensitive glob matched against the list "
        "label (e.g. 'LoreRim 2.2*') or manifest file name; adds to the "
        "config's exclude list (repeatable)",
    )
    common.add_argument(
        "--latest-only", action="store_true",
        help="Keep only the newest version of each list (grouped by list "
        "name); superseded manifests are announced",
    )
    common.add_argument(
        "--log-level", default="warning",
        choices=("debug", "info", "warning", "error"),
        help="Diagnostic verbosity on stderr (timings, per-source detail)",
    )
    return common


def _add_report(sub, common) -> None:
    rep = sub.add_parser(
        "report", parents=[common],
        help="Read-only inventory: classify downloads against manifests",
    )
    rep.add_argument("--csv", type=Path, help="Write per-file detail to this CSV")


def _add_hash(sub, common) -> None:
    hsh = sub.add_parser(
        "hash", parents=[common],
        help="Compute and cache file hashes (resumable; safe to interrupt)",
    )
    hsh.add_argument("--limit", type=int, help="Stop after hashing this many files")
    hsh.add_argument(
        "--only-candidates", action="store_true",
        help="Hash only files currently classified stale/unclaimed",
    )


def _add_sweep(sub, common) -> None:
    swp = sub.add_parser(
        "sweep", parents=[common],
        help="Move deletion candidates to quarantine (dry run unless --apply)",
    )
    swp.add_argument("--quarantine", type=Path, help="Quarantine directory")
    swp.add_argument(
        "--apply", action="store_true",
        help="Actually move files (default is a dry run)",
    )
    swp.add_argument(
        "--delete", action="store_true",
        help="Purge the batch immediately after quarantining - NO UNDO; "
        "requires --apply",
    )


def _add_restore(sub) -> None:
    rst = sub.add_parser("restore", help="Move a quarantined sweep batch back")
    rst.add_argument("batch", type=Path, help="Batch directory created by sweep")


def _add_snapshot(sub, common) -> None:
    snp = sub.add_parser(
        "snapshot", parents=[common],
        help="Export each active source as a compact JSON whitelist that "
        "survives deletion of the original manifest",
    )
    snp.add_argument(
        "--out", type=Path, default=Path("snapshots"), help="Output directory"
    )


def _add_purge(sub) -> None:
    prg = sub.add_parser(
        "purge",
        help="Permanently delete quarantine batches older than a trust "
        "period (dry run unless --apply)",
    )
    prg.add_argument("--config", type=Path, help="Config file")
    prg.add_argument("--quarantine", type=Path, help="Quarantine directory")
    prg.add_argument(
        "--older-than", type=int, metavar="DAYS",
        help="Age threshold in days (default: [quarantine] keep_days in "
        "config, else 30)",
    )
    prg.add_argument(
        "--apply", action="store_true",
        help="Actually delete (default is a dry run)",
    )


# --- source resolution ------------------------------------------------------


@dataclass
class Resolved:
    downloads: Path
    # (kind, path, pinned); kind: wabbajack|nolvus|mo2|mo2-all|snapshot.
    # pinned = the user named this file/install itself (not found via a
    # directory walk); latest-only filtering never drops pinned sources.
    sources: list[tuple[str, Path, bool]]
    exclude: list[str]
    latest_only: bool
    cache: Path
    quarantine: Path | None
    from_config: bool  # sources came from config, not ad-hoc CLI -m args


def _resolve(args: argparse.Namespace, need_manifests: bool = True) -> Resolved:
    cfg = config.load(args.config)
    downloads = args.downloads or cfg.downloads
    if downloads is None:
        raise SystemExit("error: no --downloads given and none in config")
    from_config = not (args.manifests or args.mo2_all)
    if not from_config:
        # Explicit CLI sources replace the config's source set entirely.
        sources = _expand_cli(args.manifests or [])
        sources += _expand_installs(args.mo2_all or [], "mo2-all")
    else:
        sources = config_sources(cfg)
    if need_manifests and not sources:
        raise SystemExit("error: no manifest sources (-m) given and none in config")
    return Resolved(
        downloads=downloads,
        sources=sources,
        exclude=cfg.exclude + args.exclude,  # additive: CLI extends config
        latest_only=args.latest_only or cfg.latest_only,
        cache=args.cache or cfg.cache or DEFAULT_CACHE,
        quarantine=getattr(args, "quarantine", None) or cfg.quarantine,
        from_config=from_config,
    )


_LOADERS = {
    "wabbajack": wabbajack.load,
    "nolvus": nolvus.load,
    "mo2": mo2.load,
    "mo2-all": lambda p: mo2.load(p, include_all=True),
    "snapshot": snapshot_mod.load,
}


def load_manifests(
    sources: list[tuple[str, Path, bool]],
    exclude: list[str] | None = None,
    latest_only: bool = False,
    parse_cache: Path | None = None,
) -> list[Manifest]:
    """Resolve sources to active manifests (see README "Source resolution").

    Precedence: exclude > pin (explicit entry) > latest_only > active by
    default. Exclusion is checked against file name before parsing and label
    after; label dedupe keeps the first copy but a pin from any copy sticks;
    empty MO2 sources drop; the version filter never drops pinned manifests
    though they still compete as versions. Every drop or pin-save is
    announced on stderr — no silent decisions.
    """
    start = time.perf_counter()
    manifests, pinned = _load_sources(sources, exclude or [], parse_cache)
    if latest_only:
        manifests = _apply_latest_filter(manifests, pinned)
    log.info(
        "resolved %d active source(s) from %d candidate(s) in %.2fs",
        len(manifests), len(sources), time.perf_counter() - start,
    )
    return manifests


def _load_sources(
    sources: list[tuple[str, Path, bool]],
    exclude: list[str],
    parse_cache: Path | None = None,
) -> tuple[list[Manifest], set[str]]:
    manifests: dict[str, Manifest] = {}
    pinned: set[str] = set()
    for kind, path, pin in sources:
        manifest = _load_source(kind, path, exclude, parse_cache)
        if manifest is None:
            continue
        # The same list version often exists under several Wabbajack installs;
        # keep the first copy of each label — but a pin from any copy sticks.
        manifests.setdefault(manifest.label, manifest)
        if pin:
            pinned.add(manifest.label)
    return list(manifests.values()), pinned


_PARSE_CACHEABLE = ("wabbajack", "nolvus", "snapshot")


def _load_source(
    kind: str, path: Path, exclude: list[str], parse_cache: Path | None = None
) -> Manifest | None:
    pattern = _excluded_by(path.name, exclude)
    if pattern:
        print(f"excluded ({pattern}): {path.name}", file=sys.stderr)
        return None
    cacheable = parse_cache is not None and kind in _PARSE_CACHEABLE
    manifest = manifest_cache.load(parse_cache, path, kind) if cacheable else None
    if manifest is None:
        start = time.perf_counter()
        try:
            manifest = _LOADERS[kind](path)
        except Exception as exc:  # a bad manifest shouldn't sink the run
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            return None
        log.debug(
            "loaded %s (%s): %d entries in %.2fs",
            manifest.label, kind, len(manifest.entries), time.perf_counter() - start,
        )
        if cacheable:
            manifest_cache.store(parse_cache, path, kind, manifest)
    pattern = _excluded_by(manifest.label, exclude)
    if pattern:
        print(f"excluded ({pattern}): {manifest.label}", file=sys.stderr)
        return None
    if kind in ("mo2", "mo2-all") and not manifest.entries:
        # Installs matter only when they carry custom additions.
        return None
    return manifest


def _apply_latest_filter(
    manifests: list[Manifest], pinned: set[str]
) -> list[Manifest]:
    from .manifest import latest_only as filter_latest

    kept, superseded, pinned_kept = filter_latest(manifests, pinned)
    for old, winner in superseded:
        print(f"superseded by {winner.label}: {old.label}", file=sys.stderr)
    for m, winner in pinned_kept:
        print(
            f"pinned (explicit entry) despite {winner.label}: {m.label}",
            file=sys.stderr,
        )
    return kept


def _excluded_by(name: str, exclude: list[str]) -> str | None:
    from fnmatch import fnmatchcase

    for pattern in exclude:
        if fnmatchcase(name.lower(), pattern.lower()):
            return pattern
    return None


def exact_exclude_pattern(label: str) -> str:
    """A glob matching exactly this label (brackets etc. escaped, so
    '[NoDelete] X' style labels survive fnmatch)."""
    import glob

    return glob.escape(label)


def is_exact_exclude(pattern: str, label: str) -> bool:
    """True when `pattern` is the exact-label exclude for `label` (either the
    raw label or its escaped form) - i.e. safely removable to reinstate."""
    return pattern.lower() in (label.lower(), exact_exclude_pattern(label).lower())


@dataclass
class SourceInfo:
    manifest: Manifest
    state: str  # active | pinned | excluded | superseded
    detail: str = ""  # the exclude pattern, or the winning label


def survey_sources(
    sources: list[tuple[str, Path, bool]],
    exclude: list[str] | None = None,
    latest_only: bool = False,
    parse_cache: Path | None = None,
) -> list[SourceInfo]:
    """Resolve like load_manifests but drop nothing: every parseable source
    comes back tagged, so a UI can offer reinstatement of excluded or
    superseded lists. Emits no announcements (the tags carry the story)."""
    exclude = exclude or []
    manifests, pinned = _load_sources(sources, [], parse_cache)
    flags: dict[str, tuple[str, str]] = {}
    for m in manifests:
        pattern = _excluded_by(m.label, exclude) or _excluded_by(
            m.source_path.name, exclude
        )
        if pattern:
            flags[m.label] = ("excluded", pattern)
    if latest_only:
        from .manifest import latest_only as filter_latest

        remaining = [m for m in manifests if m.label not in flags]
        _, superseded, pinned_kept = filter_latest(remaining, pinned)
        for old, winner in superseded:
            flags[old.label] = ("superseded", winner.label)
        for m, winner in pinned_kept:
            flags[m.label] = ("pinned", winner.label)
    return [SourceInfo(m, *flags.get(m.label, ("active", ""))) for m in manifests]


def config_sources(cfg: config.Config) -> list[tuple[str, Path, bool]]:
    """Expand a config's typed source lists (shared by the CLI and the GUI)."""
    sources = _expand_wabbajack(cfg.wabbajack)
    sources += _expand_nolvus(cfg.nolvus)
    sources += _expand_installs(cfg.installs, "mo2")
    sources += _expand_installs(cfg.recovery, "mo2-all")
    sources += [("snapshot", p, True) for p in cfg.snapshots]
    return sources


def _expand_nolvus(paths: list[Path]) -> list[tuple[str, Path, bool]]:
    """A directory of bundled manifests is implicit (latest-only filterable);
    a file the user names — their own copy from the author — is pinned."""
    out: list[tuple[str, Path, bool]] = []
    for path in paths:
        if path.is_dir():
            files = sorted(
                p for p in path.iterdir()
                if p.name.lower().endswith((".xml", ".xml.gz"))
            )
            out.extend(("nolvus", p, False) for p in files)
        else:
            out.append(("nolvus", path, True))
    return out


def _expand_wabbajack(paths: list[Path]) -> list[tuple[str, Path, bool]]:
    out: list[tuple[str, Path, bool]] = []
    for path in paths:
        if path.is_dir():
            out.extend(("wabbajack", p, False) for p in sorted(path.rglob("*.wabbajack")))
        else:
            out.append(("wabbajack", path, True))
    return out


def _expand_installs(paths: list[Path], kind: str) -> list[tuple[str, Path, bool]]:
    """An entry is either an MO2 install itself, or a folder whose direct
    children are installs. Anything else is warned about, never guessed at."""
    out: list[tuple[str, Path, bool]] = []
    for path in paths:
        if _is_mo2_instance(path):
            out.append((kind, path, True))
            continue
        instances = [
            c for c in sorted(path.iterdir()) if c.is_dir() and _is_mo2_instance(c)
        ] if path.is_dir() else []
        if instances:
            out.extend((kind, c, False) for c in instances)
        else:
            print(f"warning: {path}: no MO2 install (mods/) found", file=sys.stderr)
    return out


def _expand_cli(paths: list[Path]) -> list[tuple[str, Path, bool]]:
    """CLI -m convenience: infer the source type per argument."""
    out: list[tuple[str, Path, bool]] = []
    for path in paths:
        if not path.is_dir():
            kind = _infer_file_kind(path)
            if kind is None:
                print(f"warning: {path}: unrecognized manifest type", file=sys.stderr)
            else:
                out.append((kind, path, True))
            continue
        if _is_mo2_instance(path):
            out.append(("mo2", path, True))
            continue
        instances = [
            c for c in sorted(path.iterdir()) if c.is_dir() and _is_mo2_instance(c)
        ]
        if instances:
            out.extend(("mo2", c, False) for c in instances)
        else:
            out.extend(("wabbajack", p, False) for p in sorted(path.rglob("*.wabbajack")))
    return out


def _infer_file_kind(path: Path) -> str | None:
    name = path.name.lower()
    if name.endswith(".wabbajack"):
        return "wabbajack"
    if name.endswith(".json"):
        return "snapshot" if snapshot_mod.is_snapshot(path) else "wabbajack"
    if name.endswith((".xml", ".xml.gz")):
        return "nolvus"
    return None


def _is_mo2_instance(path: Path) -> bool:
    if not path.is_dir():
        return False
    return path.name.lower() == "mods" or any(
        c.is_dir() and c.name.lower() == "mods" for c in path.iterdir()
    )


# --- commands ----------------------------------------------------------------


def _check_source_drift(res: Resolved, manifests: list[Manifest]) -> None:
    """Warn when a previously-active source can no longer be found on disk.

    Only config-driven runs check and update the baseline — an ad-hoc -m
    subset would both false-positive and clobber it.
    """
    if not res.from_config:
        return
    state_path = res.cache.parent / state.STATE_NAME
    previous = state.read(state_path)
    for label, source in state.vanished(previous, manifests):
        print(
            f"warning: previously-active source vanished: {label} "
            f"({source} no longer exists) - its archives are no longer "
            f"protected; restore the file or point the config at a snapshot",
            file=sys.stderr,
        )
    state.write(state_path, manifests)


def _cmd_report(args: argparse.Namespace) -> int:
    res = _resolve(args)
    manifests = load_manifests(
        res.sources, res.exclude, res.latest_only, res.cache.parent / "manifest_cache"
    )
    if not manifests:
        print("No manifests found.", file=sys.stderr)
        return 1
    _check_source_drift(res, manifests)
    files = scan(res.downloads)
    cache = HashCache(res.cache)
    try:
        results = match(files, manifests, cache)
    finally:
        cache.close()
    print(summarize(results, manifests))
    if args.csv:
        write_csv(results, args.csv)
        print(f"\nDetail written to {args.csv}")
    return 0


def _cmd_hash(args: argparse.Namespace) -> int:
    res = _resolve(args, need_manifests=args.only_candidates)
    files = [f for f in scan(res.downloads) if not f.is_meta]
    cache = HashCache(res.cache)
    try:
        if args.only_candidates:
            files = _candidate_files(files, res, cache)
        pending = [f for f in files if cache.get(f) is None]
        if args.limit:
            pending = pending[: args.limit]
        total_bytes = sum(f.size for f in pending)
        print(
            f"{len(files):,} files scanned; {len(pending):,} to hash "
            f"({total_bytes / (1 << 30):,.1f} GB)"
        )
        _hash_files(pending, total_bytes, cache)
    except KeyboardInterrupt:
        print("\nInterrupted - progress is cached; rerun to resume.")
    finally:
        cache.close()
    return 0


def _candidate_files(
    files: list[DiskFile], res: Resolved, cache: HashCache
) -> list[DiskFile]:
    from .matcher import STALE, UNCLAIMED

    manifests = load_manifests(
        res.sources, res.exclude, res.latest_only, res.cache.parent / "manifest_cache"
    )
    results = match(files, manifests, cache)
    wanted = {
        r.disk.rel for r in results if r.status in (STALE, UNCLAIMED) and not r.sidecar
    }
    return [f for f in files if f.rel in wanted]


def _hash_files(pending: list[DiskFile], total_bytes: int, cache: HashCache) -> None:
    from .hashutil import hash_file

    done_bytes = 0
    start = time.monotonic()
    for i, disk in enumerate(pending, 1):
        xxh64_b64, crc32 = hash_file(disk.path)
        cache.put(disk, xxh64_b64, crc32)
        done_bytes += disk.size
        if i % 50 == 0 or i == len(pending):
            elapsed = time.monotonic() - start
            rate = done_bytes / elapsed if elapsed else 0
            remaining = (total_bytes - done_bytes) / rate if rate else 0
            print(
                f"  {i:,}/{len(pending):,} files  "
                f"{done_bytes / (1 << 30):,.1f}/{total_bytes / (1 << 30):,.1f} GB  "
                f"{rate / (1 << 20):,.0f} MB/s  ~{remaining / 60:,.0f} min left",
                flush=True,
            )


def _cmd_sweep(args: argparse.Namespace) -> int:
    if args.delete and not args.apply:
        raise SystemExit("error: --delete requires --apply")
    res = _resolve(args)
    quarantine = _quarantine_dir_for(res.downloads, res.quarantine)
    manifests = load_manifests(
        res.sources, res.exclude, res.latest_only, res.cache.parent / "manifest_cache"
    )
    if not manifests:
        print("No manifests found.", file=sys.stderr)
        return 1
    _check_source_drift(res, manifests)
    plan = _build_plan(res, manifests)
    _print_plan(plan, quarantine)
    if not plan.ready:
        return 0
    if not args.apply:
        print("\nDry run - nothing moved. Rerun with --apply to quarantine.")
        return 0
    return _apply_sweep(plan, quarantine, delete=args.delete)


def _quarantine_dir_for(downloads: Path, quarantine: Path | None) -> Path:
    if quarantine is None:
        quarantine = downloads.parent / "_quarantine"
    quarantine = Path(quarantine)
    if quarantine.resolve().is_relative_to(Path(downloads).resolve()):
        raise SystemExit("error: quarantine dir must not be inside the downloads dir")
    return quarantine


def _build_plan(res: Resolved, manifests: list[Manifest]) -> sweep_mod.Plan:
    files = scan(res.downloads)
    cache = HashCache(res.cache)
    try:
        results = match(files, manifests, cache)
        return sweep_mod.plan(results, cache)
    finally:
        cache.close()


def _print_plan(p: sweep_mod.Plan, quarantine: Path) -> None:
    print(
        f"Sweep plan: {len(p.ready):,} files, {p.ready_bytes / (1 << 30):,.2f} GB "
        f"-> {quarantine}"
    )
    if p.refused:
        print(
            f"Refused (hash never checked): {len(p.refused):,} files, "
            f"{p.refused_bytes / (1 << 30):,.2f} GB - run "
            f"`modsweep hash --only-candidates` first"
        )
    largest = sorted(
        (r for r in p.ready if not r.disk.is_meta),
        key=lambda r: r.disk.size, reverse=True,
    )
    for r in largest[:10]:
        print(f"  {r.disk.size / (1 << 30):>8.2f} GB  [{r.status}]  {r.disk.rel}")
    if len(largest) > 10:
        print(f"  ... and {len(largest) - 10:,} more archives (+ sidecars)")


def _apply_sweep(p: sweep_mod.Plan, quarantine: Path, delete: bool) -> int:
    batch = sweep_mod.execute(p, quarantine)
    if delete:
        sweep_mod.purge_batch(batch)
        print(
            f"\nDeleted {len(p.ready):,} files "
            f"({p.ready_bytes / (1 << 30):,.2f} GB) - quarantined and purged "
            f"immediately; there is no undo"
        )
        return 0
    print(f"\nMoved {len(p.ready):,} files to {batch}")
    print(f"Undo with: modsweep restore \"{batch}\"")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    moved, skipped, missing = sweep_mod.restore(args.batch)
    print(f"Restored {moved:,} files.")
    if skipped:
        print(f"{skipped:,} skipped: original path already occupied (left in quarantine).")
    if missing:
        print(f"{missing:,} listed in the manifest were not found in the batch.")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    res = _resolve(args)
    manifests = load_manifests(
        res.sources, res.exclude, res.latest_only, res.cache.parent / "manifest_cache"
    )
    if not manifests:
        print("No manifests found.", file=sys.stderr)
        return 1
    for manifest in manifests:
        path = snapshot_mod.save(manifest, args.out)
        print(f"  {manifest.label}  ({len(manifest.entries)} entries) -> {path}")
    print(f"\n{len(manifests)} snapshot(s) written to {args.out}")
    return 0


def _cmd_purge(args: argparse.Namespace) -> int:
    from datetime import datetime, timedelta

    cfg = config.load(args.config)
    quarantine = args.quarantine or cfg.quarantine
    if quarantine is None:
        raise SystemExit("error: no --quarantine given and none in config")
    days = _purge_threshold(args, cfg)
    batches = sweep_mod.list_batches(quarantine)
    if not batches:
        print(f"No sweep batches under {quarantine}.")
        return 0
    cutoff = datetime.now() - timedelta(days=days)
    aged = _print_batch_verdicts(batches, cutoff, days)
    if not aged:
        return 0
    if not args.apply:
        print("Dry run - nothing deleted. Rerun with --apply to purge.")
        return 0
    for b in aged:
        sweep_mod.purge_batch(b)
        print(f"purged {b.path}")
    return 0


def _purge_threshold(args: argparse.Namespace, cfg: config.Config) -> int:
    if args.older_than is not None:
        return args.older_than
    if cfg.quarantine_keep_days is not None:
        return cfg.quarantine_keep_days
    return 30


def _print_batch_verdicts(batches, cutoff, days: int) -> list[sweep_mod.Batch]:
    from datetime import datetime

    aged = [b for b in batches if b.created < cutoff]
    for b in batches:
        age = (datetime.now() - b.created).days
        verdict = "purge" if b.created < cutoff else "keep"
        print(
            f"  [{verdict}]  {b.path.name}  {age:>4}d old  "
            f"{b.files:,} files  {b.size / (1 << 30):,.2f} GB"
        )
    total = sum(b.size for b in aged)
    print(
        f"\n{len(aged)} of {len(batches)} batch(es) older than {days} days "
        f"({total / (1 << 30):,.2f} GB)"
    )
    return aged


if __name__ == "__main__":
    sys.exit(main())
