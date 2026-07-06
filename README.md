# mod-sweep

Whitelist-driven cleanup for a Skyrim archive directory shared by multiple
modlists (Wabbajack lists + Nolvus). Builds a union whitelist from modlist
manifests, classifies every file in the downloads directory against it, and
reports what is claimed, stale, or unclaimed. **Read-only for now — there is
deliberately no delete command until the inventory is trusted.**

## Layout convention (verified)

- Wabbajack lists dump all archives into the downloads **root**.
- Nolvus places mods into **category subdirectories** (`1.1 SKSE PLUGINS`, …)
  that match `<Category><Name>` in its manifest; only its shared tools
  (Mod Organizer, SSEEdit, BSArch) land in the root.
- `.meta` files are sidecars; they follow the fate of the archive next to them.
- User-added mods live in MO2 mod folders prefixed `[NoDelete]` (usually
  `[NoDelete] 00.000 {mod name}`); each folder's `meta.ini` records the source
  archive (`installationFile=`), which is protected in the downloads dir.

## Manifest formats (verified against real files)

| | Wabbajack (`modlist.json` inside `.wabbajack` zip) | Nolvus (`InstallPackage.xml`) |
|---|---|---|
| Size | exact bytes | `round(bytes / 1024)` — sanity check only |
| Hash | xxHash64, base64 of the little-endian digest | CRC32 (hex) — authoritative |
| Location | root | `Softwares` → root, `Categories` → subdir |

Matching is by file name first, but a cached hash always wins: Wabbajack
identifies archives purely by hash, so renamed files (e.g. sha256 embedded in
the name) are rescued by the hash index. Subdirectory mismatches are reported
as notes, never used to disqualify (Nolvus renumbers categories between
versions).

## Usage

Environment is managed with `uv` (`uv sync` once, then `uv run modsweep ...`).
`modsweep.toml` declares the downloads dir, the active sources of truth, and
the quarantine dir — with it in place the commands need no arguments. Source
types are explicit keys (`wabbajack`, `nolvus`, `installs`, `recovery`);
nothing in the config is auto-detected, so scattered multi-drive setups just
list each install explicitly. CLI flags override config (`-m` does per-arg
type inference for convenience; see `modsweep <cmd> --help`).

```powershell
uv run modsweep report --csv reports\inventory.csv   # read-only inventory
uv run modsweep hash --only-candidates               # hash-check candidates (pre-delete gate)
uv run modsweep hash                                 # full hash pass (optional; resumable)
uv run modsweep sweep                                # dry run: what would be quarantined
uv run modsweep sweep --apply                        # move candidates to quarantine
uv run modsweep restore <quarantine\batch-dir>       # undo a sweep batch
uv run modsweep snapshot                             # export durable whitelists
```

The hash cache lives in `.modsweep/hashes.sqlite`, keyed by path and
invalidated when size or mtime changes.

## Retiring a list

Every manifest found under the configured sources is **active by default** —
the safe failure mode: a forgotten list keeps its files rather than losing
them. The report header names each active source (label + version + origin
path); that list is the thing to review. To retire one:

- set `latest_only = true` (or pass `--latest-only`) to keep just the newest
  version of every list, grouped by list name — superseded manifests are
  announced. Explicit file entries override the filter: naming a specific
  `.wabbajack` (even redundantly alongside its directory) pins that version
  as intentional, so "latest by default, plus these specific versions" is
  one directory line plus one line per pin. Pinned files still compete as
  versions — pinning the newest does not resurrect older ones. Excluding
  the newest version also pins an older one; or
- add an `exclude` glob in `modsweep.toml` (or ad-hoc via `--exclude`),
  matched case-insensitively against the list label (`'LoreRim 2.2*'`) or
  the manifest file name — the .wabbajack stays on disk for reinstatement; or
- delete/move the `.wabbajack` out of the searched directory; or
- replace a directory entry with explicit file paths and omit it.

Then:

1. Check the report's per-source `unique` column — that's what retiring frees.
2. `uv run modsweep report` to preview, `sweep` (dry run), then
   `sweep --apply`. Sweep refuses files whose hash was never checked and
   never hard-deletes: batches land under the quarantine dir with a
   restore manifest, and `restore` puts a batch back untouched.

`installs` entries are only needed where `[NoDelete]` custom additions exist —
an install without them contributes nothing and is dropped from evaluation.

## Platform support

Cross-platform is a standing requirement: Windows is the primary platform,
Linux support matters (modlist tooling increasingly runs there), and macOS
must not be broken even though MO2 itself doesn't run on it. Concretely:

- All filesystem work goes through `pathlib`/`os` — no OS-specific APIs.
- Relative paths inside modsweep (scan results, sweep batches, CSVs) use `/`
  on every platform; `pathlib` accepts it on Windows too.
- Name matching is case-insensitive (Windows semantics). On case-sensitive
  filesystems this only errs toward *keeping* files — the safe direction.
- `meta.ini` values may contain Windows-style paths even when read on POSIX
  (installs created under Wine/Proton), so they are split on both separators.
- Console output sticks to ASCII; file output is UTF-8.

## Hashing policy

Classification is name/size by default — `report` never computes hashes, it
only reads the cache, so a cold run is stat-speed. Hashing exists for one
reason: Wabbajack identifies archives by hash and sometimes renames them on
disk, so name-only matching produces false deletion candidates (27.6 GB worth
on the reference disk, some shadowed by stale same-named siblings). Hence:
hash-check the *candidates* before deleting (`hash --only-candidates`, ~1 min)
rather than the whole directory. The future delete command must refuse to
remove any file whose hash was never checked against the whitelist.

## Sources of truth

- `.wabbajack` / `modlist.json` is the only *complete* truth (name + size +
  hash). Keep the .wabbajack files — at 25–70 MB they are tiny next to the
  archives they describe.
- An installation alone still yields a *name-level* whitelist: every MO2 mod
  folder's `meta.ini` records `installationFile=`. Use `--mo2-all <install>`
  for lists whose .wabbajack is gone (no version discrimination beyond the
  file name, which usually embeds version + Nexus file id).
- Wabbajack's `%LOCALAPPDATA%\Wabbajack\GlobalHashCache2.sqlite` maps local
  paths to xxHash64 but knows nothing about list membership — a cache, not a
  manifest.

`modsweep snapshot` exports each active source as a compact JSON whitelist
(name/size/hash per entry). Snapshots load like any other source (the
`snapshots` config key, or `-m file.json`), so a list stays retirable and
reinstatable even after its original .wabbajack is deleted.

## Roadmap

- Quarantine aging: purge batches after a trust period.
- GUI for picking manifests and watching progress (nice-to-have).
