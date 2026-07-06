# mod-sweep

[![CI](https://github.com/zspatter/mod-sweep/actions/workflows/ci.yml/badge.svg)](https://github.com/zspatter/mod-sweep/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/modsweep)](https://pypi.org/project/modsweep/)

![The report view: checkable source tree and classification tables](https://raw.githubusercontent.com/zspatter/mod-sweep/main/docs/screenshots/report.png)

Whitelist-driven cleanup for a Skyrim archive directory shared by multiple
modlists (Wabbajack lists + Nolvus). Builds a union whitelist from modlist
manifests, classifies every file in the downloads directory against it, and
reports what is claimed, stale, or unclaimed. **Read-only for now - there is
deliberately no delete command until the inventory is trusted.**

## Installation

- **Standalone executables** (no Python required): grab the archive for
  your platform from the [latest release](https://github.com/zspatter/mod-sweep/releases/latest) -
  each contains the `modsweep` CLI and the `modsweep-gui` app.
- **PyPI**: `uv tool install "modsweep[gui]"` (or
  `pipx install "modsweep[gui]"`) installs both commands; drop the `[gui]`
  extra for the CLI only. Upgrade later with `uv tool upgrade modsweep`.
- **From source**: clone, `uv sync --extra gui`, then `uv run modsweep` /
  `uv run modsweep-gui`.

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
| Size | exact bytes | `round(bytes / 1024)` - sanity check only |
| Hash | xxHash64, base64 of the little-endian digest | CRC32 (hex) - authoritative |
| Location | root | `Softwares` → root, `Categories` → subdir |

Matching is by file name first, but a cached hash always wins: Wabbajack
identifies archives purely by hash, so renamed files (e.g. sha256 embedded in
the name) are rescued by the hash index. Subdirectory mismatches are reported
as notes, never used to disqualify (Nolvus renumbers categories between
versions).

## Usage

Environment is managed with `uv` (`uv sync` once, then `uv run modsweep ...`).
`modsweep.toml` declares the downloads dir, the active sources of truth, and
the quarantine dir - with it in place the commands need no arguments. Source
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
uv run modsweep sweep --apply --delete               # ...and purge immediately (no undo)
uv run modsweep restore <quarantine\batch-dir>       # undo a sweep batch
uv run modsweep snapshot                             # export durable whitelists
uv run modsweep purge                                # age out old quarantine batches
uv run modsweep update-manifests                     # fetch newly published Nolvus manifests
uv run modsweep check-update                         # newer release on GitHub?
```

The hash cache lives in `.modsweep/hashes.sqlite`, keyed by path and
invalidated when size or mtime changes - so a file that changes after
hashing automatically counts as unhashed again and sweeps refuse it until
it is re-hashed. All commands accept `--log-level debug|info` for
diagnostics on stderr (timings, per-source parse detail).

## Source resolution

Every command resolves its active sources through the same pipeline. The
precedence is: **exclude > pin (explicit entry) > latest_only > active by
default** - and every decision is announced on stderr, never silent.

1. **Discovery.** Config keys (or CLI `-m`) expand to concrete manifests.
   Directory entries are walked: a `wabbajack` dir is searched recursively
   for `.wabbajack`; an `installs` entry is either an MO2 install or a
   folder searched a few levels deep for installs (Nolvus nests its
   instances under `Instances\<name>\MODS`, and the whole wrapped
   instance layout is handled). Provenance is recorded here -
   a file or install you named yourself is *explicit* (pinned); anything
   found by a directory walk is *implicit*. Nolvus manifests and snapshots
   are always named directly, so they are always pinned.
2. **Exclusion.** `exclude` globs (config plus `--exclude`, additive) match
   case-insensitively against the manifest file name (before parsing) and
   the list label (after parsing). An excluded manifest takes no further
   part - exclusion beats pinning. Announced as `excluded (<pattern>)`.
3. **Dedupe.** Identical labels - the same list version found in several
   places, e.g. two Wabbajack install dirs - collapse to one manifest.
   A pin from *any* copy sticks.
4. **Empty installs.** Installs with no `[NoDelete]` entries contribute
   nothing and are dropped from evaluation.
5. **Version filter** (only when `latest_only`). Manifests group by list
   name; the highest version per group survives (numeric-aware compare,
   suffixes tolerated, empty version lowest). Pinned manifests are never
   dropped, but they still *compete* as versions, so pinning the newest
   does not resurrect older ones. Versionless sources ([NoDelete]
   instances) are single-member groups and always survive. Announced as
   `superseded by <winner>` and `pinned (explicit entry) despite <winner>`.

Whatever survives is active, and every file an active source names is
protected. The failure mode is deliberately asymmetric: forgetting a list
keeps its files; only explicit action (exclusion, or an old version losing
under `latest_only`) exposes files for sweeping.

**Drift detection.** Config-driven runs of `report` and `sweep` record the
active set in `.modsweep/state.json`. When a previously-active manifest can
no longer be found on disk (e.g. Wabbajack was uninstalled, taking its
`downloaded_mod_lists` along), the next run warns -
`previously-active source vanished: <label>` - instead of silently treating
its archives as unclaimed. The warning fires once; the baseline then accepts
the new set. Ad-hoc `-m` runs neither check nor update the baseline, so
what-if experiments cannot clobber it.

Example - "latest of everything, except keep old LoreRim too, and retire
NGVO entirely":

```toml
latest_only = true
exclude = ['NGVO*']
wabbajack = [
    'C:\Games\modding_tools\Wabbajack',                # implicit: latest wins
    'C:\...\LoreRim_@@_LoreRim.wabbajack',             # explicit: pinned
]
```

## Retiring a list

Every manifest found under the configured sources is **active by default** -
the safe failure mode: a forgotten list keeps its files rather than losing
them. The report header names each active source (label + version + origin
path); that list is the thing to review. To retire one:

- set `latest_only = true` (or pass `--latest-only`) to keep just the newest
  version of every list, pinning specific versions by naming their files
  explicitly (see Source resolution above); or
- add an `exclude` glob in `modsweep.toml` (or ad-hoc via `--exclude`),
  matched case-insensitively against the list label (`'LoreRim 2.2*'`) or
  the manifest file name - the .wabbajack stays on disk for reinstatement; or
- delete/move the `.wabbajack` out of the searched directory; or
- replace a directory entry with explicit file paths and omit it.

Then:

1. Check the report's per-source `unique` column - that's what retiring frees.
2. `uv run modsweep report` to preview, `sweep` (dry run), then
   `sweep --apply`. Sweep refuses files whose hash was never checked and
   never hard-deletes: batches land under the quarantine dir with a
   restore manifest, and `restore` puts a batch back untouched.

`installs` entries are only needed where `[NoDelete]` custom additions exist -
an install without them contributes nothing and is dropped from evaluation.

## Platform support

Cross-platform is a standing requirement: Windows is the primary platform,
Linux support matters (modlist tooling increasingly runs there), and macOS
must not be broken even though MO2 itself doesn't run on it. Concretely:

- All filesystem work goes through `pathlib`/`os` - no OS-specific APIs.
- Relative paths inside modsweep (scan results, sweep batches, CSVs) use `/`
  on every platform; `pathlib` accepts it on Windows too.
- Name matching is case-insensitive (Windows semantics). On case-sensitive
  filesystems this only errs toward *keeping* files - the safe direction.
- `meta.ini` values may contain Windows-style paths even when read on POSIX
  (installs created under Wine/Proton), so they are split on both separators.
- Console output sticks to ASCII; file output is UTF-8.

## Hashing policy

Classification is name/size by default - `report` never computes hashes, it
only reads the cache, so a cold run is stat-speed. Hashing exists for one
reason: Wabbajack identifies archives by hash and sometimes renames them on
disk, so name-only matching produces false deletion candidates (27.6 GB worth
on the reference disk, some shadowed by stale same-named siblings). Hence:
hash-check the *candidates* before deleting (`hash --only-candidates`, ~1 min)
rather than the whole directory. The future delete command must refuse to
remove any file whose hash was never checked against the whitelist.

## Bundled Nolvus manifests

Nolvus `InstallPackage.xml` files are not distributed publicly, so this
project bundles them (gzipped, ~2 MB each) as package data - **please do
not contact the Nolvus author for these files**; new guide releases are
contributed to this project instead. The `bundled` entry in the `nolvus`
config key resolves to the shipped manifests plus a per-user data dir where
`modsweep update-manifests` (or Tools > Update Nolvus Manifests in the GUI)
downloads newly published versions straight from this repository - no
servers involved beyond GitHub, and no new executable required for a
manifest bump. Bundled manifests load implicitly (so `latest_only`
applies); if you have your own copy, point the `nolvus` config key at the
`.xml` file directly and it is pinned like any explicit entry. The parser
reads both `.xml` and `.xml.gz`.

The Nolvus author's upcoming sibling list is expected to ship the same
manifest format; supporting it should be a matter of dropping its XML into
the bundle (see Roadmap).

## Sources of truth

- `.wabbajack` / `modlist.json` is the only *complete* truth (name + size +
  hash). Keep the .wabbajack files - at 25–70 MB they are tiny next to the
  archives they describe.
- **Protection lasts only while the manifest is discoverable.** Wabbajack
  sweeps rely on the `.wabbajack` files being present: uninstalling
  Wabbajack, or deleting the files themselves, implicitly retires the
  affected list versions and their uniquely-claimed archives become sweep
  candidates on the next run. Drift detection (see Source resolution)
  warns once when this happens, but cannot bring the whitelist back -
  before uninstalling Wabbajack, either copy the `.wabbajack` files
  somewhere the config points at or run `modsweep snapshot`.
- An installation alone still yields a *name-level* whitelist: every MO2 mod
  folder's `meta.ini` records `installationFile=`. Use `--mo2-all <install>`
  for lists whose .wabbajack is gone (no version discrimination beyond the
  file name, which usually embeds version + Nexus file id).
- Wabbajack's `%LOCALAPPDATA%\Wabbajack\GlobalHashCache2.sqlite` maps local
  paths to xxHash64 but knows nothing about list membership - a cache, not a
  manifest.

`modsweep snapshot` exports each active source as a compact JSON whitelist
(name/size/hash per entry). Snapshots load like any other source (the
`snapshots` config key, or `-m file.json`), so a list stays retirable and
reinstatable even after its original .wabbajack is deleted.

`modsweep purge` ages out quarantine batches after a trust period
(`keep_days` under `[quarantine]`, default 30, or `--older-than`). It is the
only hard-delete path in the tool: dry run by default, `--apply` to act, and
only directories carrying a sweep manifest are ever considered. Note the
split in semantics: `keep_days` governs only this age-based CLI purge - the
GUI's Purge button deletes exactly the batch you pick, any age, after its
confirmation (which calls out batches still younger than the trust period).

For users who trust the cleanup and want the space back now,
`sweep --apply --delete` composes the two steps: the batch is quarantined -
so every safety rule still applies (hash gate, sidecar binding, dry-run
preview, batch manifest) - and then purged immediately. **There is no undo.**
The default remains quarantine + trust period.

## GUI

```powershell
uv sync --extra gui          # installs PySide6
uv run modsweep-gui          # optional: pass a config path
```

![The config editor: pickers, per-kind source tabs, exclude globs](https://raw.githubusercontent.com/zspatter/mod-sweep/main/docs/screenshots/config-editor.png)

The GUI is a thin front-end over the same pipeline: it reads `modsweep.toml`
(Open Config... to switch) and surveys every resolvable source into a
checkable list - active and pinned sources are ticked; excluded and
superseded ones stay visible but unticked, each explaining itself on hover.
Untick lists to retire them (All/None for bulk) and Apply Selection writes
exact-label excludes to the config for you; ticking an excluded list
reinstates it. Explicit decisions carry icons - a pin for explicitly-named
sources, a ban sign for exclusions, a padlock for versions locked by
latest-only - and locked entries also say why inline (italic suffix), with
tooltips explaining the way out. Lists group alphabetically with the newest
version as the row and older versions nested beneath ("[+N older]"),
collapsed unless a child carries a pin or exclusion - so a growing manifest
bundle (the in-repo Nolvus manifests gain a file per guide release) stays
one row per list while every version remains one expander away.
Right-clicking a source offers the moves its state allows: pin a version -
superseded to rescue it, or active to protect it from future filters -
unpin, retire or reinstate a list, and open the manifest's location.
A Snapshot... button exports the active sources' durable whitelists to a
chosen folder. Action results pop up as dialogs in addition to the status
bar and Log, so outcomes are unmissable. Edit Config...
opens a full editor - downloads/quarantine folder pickers, purge trust
period, latest-only toggle, per-kind source tabs (Wabbajack / Nolvus /
Installs / Recovery / Snapshots) with add-file/add-folder buttons, and the
exclude list - so a new user can go from empty config to first report
without touching TOML; saving rewrites `modsweep.toml` (comments are
regenerated) and reloads the sources. The Report tab
renders the classification as sortable tables (status summary, per-source
claims with the unique-holdings column, all deletion candidates); the Log
tab collects resolution announcements and action output. A welcome dialog
walks through the workflow on launch (suppressible, persisted via QSettings)
and every button carries a hover tooltip. All actions - Report, Hash
Candidates, Sweep dry run, Sweep + Apply (confirmation), Restore (batch
picker), and Purge (batch picker plus a strongly-worded confirmation; the
only permanent deletion) - run on a worker thread with an indeterminate
progress bar and status-bar summaries, so the window never looks frozen and
every action visibly reports its outcome even when there was nothing to do.
State-changing actions re-run the report automatically. Candidate rows
explain their status on hover and carry a context menu: open in the file
manager, quarantine just that file, or delete it (confirmation, no undo) -
single-file batches reuse the normal batch machinery, so they remain
restorable and purgeable like any sweep. Announcements and pipeline timings
stream into the Log tab live as actions run. No custom palette or stylesheet is set anywhere, so the interface
follows the system light/dark theme natively.

## Testing

`uv run pytest` (or with `--cov=modsweep` for coverage, ~94%). The suite
spans unit tests per module and end-to-end tests that drive the CLI against
synthetic download trees - `tests/test_e2e.py` walks one tree through the
full lifecycle: pre-hash classification, hash-gate refusal, hashing,
rescue/contradiction outcomes, sweep, restore, and purge aging. Shared
builders for fake manifests live in `tests/helpers.py`. CI runs everything
on Windows/Linux/macOS × Python 3.12/3.14.

## License

MIT - do what you like with it, keep the attribution (the copyright notice
in [LICENSE](LICENSE)).

## Roadmap

- NexusMods listing (the end goal for reaching modlist users): mod page
  under Skyrim SE utilities, Windows zip from the GitHub release uploaded
  to Nexus, GitHub linked for source/other platforms/issues.
- App self-update beyond notify-and-link (exe self-replacement) if users
  ask for it; package-manager installs already upgrade via uv/pipx.
- Performance note (settled): parsed manifests are cached under
  `.modsweep/manifest_cache` keyed by source size/mtime (12.5s → 0.9s
  resolution on the reference setup). Parallel hashing deliberately
  skipped: hashing is drive-bound (~1.3 GB/s observed), threads would only
  help on fast NVMe and would actively hurt on HDDs.
- Nolvus sibling list: the author's next guide is expected to use the same
  InstallPackage format - bundle its manifests as they are released.
