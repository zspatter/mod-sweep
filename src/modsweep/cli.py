"""modsweep command-line interface.

`report` and `hash` are read-only against the downloads directory. `sweep`
never hard-deletes: it moves candidates to a quarantine batch that `restore`
can put back. Defaults come from modsweep.toml (see config.py); CLI arguments
override it.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, mo2, nolvus, sweep as sweep_mod, wabbajack
from .cache import HashCache
from .manifest import Manifest
from .matcher import match
from .report import summarize, write_csv
from .scanner import scan

DEFAULT_CACHE = Path(".modsweep") / "hashes.sqlite"


def main(argv: list[str] | None = None) -> int:
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

    parser = argparse.ArgumentParser(
        prog="modsweep",
        description="Whitelist-driven cleanup for shared modlist archive directories.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    rep = sub.add_parser(
        "report", parents=[common],
        help="Read-only inventory: classify downloads against manifests",
    )
    rep.add_argument("--csv", type=Path, help="Write per-file detail to this CSV")

    hsh = sub.add_parser(
        "hash", parents=[common],
        help="Compute and cache file hashes (resumable; safe to interrupt)",
    )
    hsh.add_argument("--limit", type=int, help="Stop after hashing this many files")
    hsh.add_argument(
        "--only-candidates", action="store_true",
        help="Hash only files currently classified stale/unclaimed",
    )

    swp = sub.add_parser(
        "sweep", parents=[common],
        help="Move deletion candidates to quarantine (dry run unless --apply)",
    )
    swp.add_argument("--quarantine", type=Path, help="Quarantine directory")
    swp.add_argument(
        "--apply", action="store_true",
        help="Actually move files (default is a dry run)",
    )

    rst = sub.add_parser("restore", help="Move a quarantined sweep batch back")
    rst.add_argument("batch", type=Path, help="Batch directory created by sweep")

    args = parser.parse_args(argv)
    if args.cmd == "report":
        return _cmd_report(args)
    if args.cmd == "hash":
        return _cmd_hash(args)
    if args.cmd == "sweep":
        return _cmd_sweep(args)
    if args.cmd == "restore":
        return _cmd_restore(args)
    return 2


@dataclass
class Resolved:
    downloads: Path
    sources: list[tuple[str, Path]]  # (kind, path); kind: wabbajack|nolvus|mo2|mo2-all
    exclude: list[str]
    cache: Path
    quarantine: Path | None


def _resolve(args: argparse.Namespace, need_manifests: bool = True) -> Resolved:
    cfg = config.load(args.config)
    downloads = args.downloads or cfg.downloads
    if downloads is None:
        raise SystemExit("error: no --downloads given and none in config")
    if args.manifests or args.mo2_all:
        # Explicit CLI sources replace the config's source set entirely.
        sources = _expand_cli(args.manifests or [])
        sources += _expand_installs(args.mo2_all or [], "mo2-all")
    else:
        sources = _expand_wabbajack(cfg.wabbajack)
        sources += [("nolvus", p) for p in cfg.nolvus]
        sources += _expand_installs(cfg.installs, "mo2")
        sources += _expand_installs(cfg.recovery, "mo2-all")
    if need_manifests and not sources:
        raise SystemExit("error: no manifest sources (-m) given and none in config")
    return Resolved(
        downloads=downloads,
        sources=sources,
        exclude=cfg.exclude + args.exclude,  # additive: CLI extends config
        cache=args.cache or cfg.cache or DEFAULT_CACHE,
        quarantine=getattr(args, "quarantine", None) or cfg.quarantine,
    )


def _cmd_report(args: argparse.Namespace) -> int:
    res = _resolve(args)
    manifests = load_manifests(res.sources, res.exclude)
    if not manifests:
        print("No manifests found.", file=sys.stderr)
        return 1
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
    from .hashutil import hash_file

    res = _resolve(args, need_manifests=args.only_candidates)
    files = [f for f in scan(res.downloads) if not f.is_meta]
    cache = HashCache(res.cache)
    try:
        if args.only_candidates:
            from .matcher import STALE, UNCLAIMED

            manifests = load_manifests(res.sources, res.exclude)
            results = match(files, manifests, cache)
            wanted = {
                r.disk.rel for r in results if r.status in (STALE, UNCLAIMED) and not r.sidecar
            }
            files = [f for f in files if f.rel in wanted]
        pending = [f for f in files if cache.get(f) is None]
        if args.limit:
            pending = pending[: args.limit]
        total_bytes = sum(f.size for f in pending)
        print(
            f"{len(files):,} files scanned; {len(pending):,} to hash "
            f"({total_bytes / (1 << 30):,.1f} GB)"
        )
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
    except KeyboardInterrupt:
        print("\nInterrupted - progress is cached; rerun to resume.")
    finally:
        cache.close()
    return 0


def _cmd_sweep(args: argparse.Namespace) -> int:
    res = _resolve(args)
    quarantine = res.quarantine
    if quarantine is None:
        quarantine = res.downloads.parent / "_quarantine"
    quarantine = Path(quarantine)
    if quarantine.resolve().is_relative_to(Path(res.downloads).resolve()):
        raise SystemExit("error: quarantine dir must not be inside the downloads dir")

    manifests = load_manifests(res.sources, res.exclude)
    if not manifests:
        print("No manifests found.", file=sys.stderr)
        return 1
    files = scan(res.downloads)
    cache = HashCache(res.cache)
    try:
        results = match(files, manifests, cache)
        p = sweep_mod.plan(results, cache)
    finally:
        cache.close()

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
    if not p.ready:
        return 0
    largest = sorted(
        (r for r in p.ready if not r.disk.is_meta),
        key=lambda r: r.disk.size, reverse=True,
    )
    for r in largest[:10]:
        print(f"  {r.disk.size / (1 << 30):>8.2f} GB  [{r.status}]  {r.disk.rel}")
    if len(largest) > 10:
        print(f"  ... and {len(largest) - 10:,} more archives (+ sidecars)")

    if not args.apply:
        print("\nDry run - nothing moved. Rerun with --apply to quarantine.")
        return 0
    batch = sweep_mod.execute(p, quarantine)
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


_LOADERS = {
    "wabbajack": wabbajack.load,
    "nolvus": nolvus.load,
    "mo2": mo2.load,
    "mo2-all": lambda p: mo2.load(p, include_all=True),
}


def load_manifests(
    sources: list[tuple[str, Path]], exclude: list[str] | None = None
) -> list[Manifest]:
    exclude = exclude or []
    manifests: dict[str, Manifest] = {}
    for kind, path in sources:
        pattern = _excluded_by(path.name, exclude)
        if pattern:
            print(f"excluded ({pattern}): {path.name}", file=sys.stderr)
            continue
        try:
            manifest = _LOADERS[kind](path)
        except Exception as exc:  # a bad manifest shouldn't sink the run
            print(f"warning: skipping {path}: {exc}", file=sys.stderr)
            continue
        pattern = _excluded_by(manifest.label, exclude)
        if pattern:
            print(f"excluded ({pattern}): {manifest.label}", file=sys.stderr)
            continue
        if kind in ("mo2", "mo2-all") and not manifest.entries:
            # Installs matter only when they carry custom additions.
            continue
        # The same list version often exists under several Wabbajack installs;
        # keep the first copy of each label.
        manifests.setdefault(manifest.label, manifest)
    return list(manifests.values())


def _excluded_by(name: str, exclude: list[str]) -> str | None:
    from fnmatch import fnmatchcase

    for pattern in exclude:
        if fnmatchcase(name.lower(), pattern.lower()):
            return pattern
    return None


def _expand_wabbajack(paths: list[Path]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in paths:
        if path.is_dir():
            out.extend(("wabbajack", p) for p in sorted(path.rglob("*.wabbajack")))
        else:
            out.append(("wabbajack", path))
    return out


def _expand_installs(paths: list[Path], kind: str) -> list[tuple[str, Path]]:
    """An entry is either an MO2 install itself, or a folder whose direct
    children are installs. Anything else is warned about, never guessed at."""
    out: list[tuple[str, Path]] = []
    for path in paths:
        if _is_mo2_instance(path):
            out.append((kind, path))
            continue
        instances = [
            c for c in sorted(path.iterdir()) if c.is_dir() and _is_mo2_instance(c)
        ] if path.is_dir() else []
        if instances:
            out.extend((kind, c) for c in instances)
        else:
            print(f"warning: {path}: no MO2 install (mods/) found", file=sys.stderr)
    return out


def _expand_cli(paths: list[Path]) -> list[tuple[str, Path]]:
    """CLI -m convenience: infer the source type per argument."""
    out: list[tuple[str, Path]] = []
    for path in paths:
        if not path.is_dir():
            suffix = path.suffix.lower()
            if suffix in (".wabbajack", ".json"):
                out.append(("wabbajack", path))
            elif suffix == ".xml":
                out.append(("nolvus", path))
            else:
                print(f"warning: {path}: unrecognized manifest type", file=sys.stderr)
            continue
        if _is_mo2_instance(path):
            out.append(("mo2", path))
            continue
        instances = [
            c for c in sorted(path.iterdir()) if c.is_dir() and _is_mo2_instance(c)
        ]
        if instances:
            out.extend(("mo2", c) for c in instances)
        else:
            out.extend(("wabbajack", p) for p in sorted(path.rglob("*.wabbajack")))
    return out


def _is_mo2_instance(path: Path) -> bool:
    if not path.is_dir():
        return False
    return path.name.lower() == "mods" or any(
        c.is_dir() and c.name.lower() == "mods" for c in path.iterdir()
    )


if __name__ == "__main__":
    sys.exit(main())
