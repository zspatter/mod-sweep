# Mod Sweep usage guide

Everything the CLI and the config file can do, in depth. For the desktop
app, see the [GUI tour](gui.md); for a two-minute overview, the
[README](../README.md).

## The mental model

Mod Sweep builds a **union whitelist** from every active source of truth
(Wabbajack lists, Nolvus install manifests, MO2 `[NoDelete]` additions,
snapshots), classifies every file in the downloads directory against it,
and treats only the leftovers as deletion candidates. Three rules shape
everything below:

1. **Forgetting a list keeps its files.** Every manifest found is active
   by default; only explicit action (an exclude, an old version losing
   under `latest_only`, a deleted manifest) exposes files for sweeping.
2. **Nothing is deleted outright.** Sweeps move candidates into a
   restorable, timestamped quarantine batch. `purge` is the only hard
   delete, and it is a separate, dry-run-by-default step.
3. **No sweep without a hash check.** A candidate whose hash was never
   verified against the whitelist is refused - renamed archives that a
   list still needs are rescued by hash, not lost to a name mismatch.

## Configuration

`modsweep.toml` is the durable record of your setup: the downloads dir,
the active sources of truth, and the quarantine dir. With it in place the
commands need no arguments. Commands look for it in the current directory,
or take `--config <path>`. Start from
[modsweep.example.toml](../modsweep.example.toml), or let the GUI's Edit
Config dialog write it for you. Relative paths resolve against the config
file's directory.

```toml
downloads = 'F:\Modlists\Skyrim\downloads'
latest_only = true

# .wabbajack files, or directories searched recursively for them
wabbajack = ['C:\Games\Wabbajack']

# InstallPackage .xml/.xml.gz files, or directories of them; 'bundled' =
# manifests shipped with the app plus downloaded updates
nolvus = ['bundled']

# MO2 installs checked for [NoDelete] custom additions
installs = ['D:\Modlists\Skyrim']

# installs whitelisted whole, by archive NAME only (recovery fallback)
recovery = []

# snapshot JSONs exported by `modsweep snapshot`
snapshots = []

# retire lists: globs matched against the label or manifest file name
exclude = []

[quarantine]
dir = 'F:\Modlists\Skyrim\_quarantine'
keep_days = 30
```

### Key reference

| Key | Meaning |
|---|---|
| `downloads` | The shared archive directory to classify and sweep. |
| `cache` | Hash-cache path (default `.modsweep/hashes.sqlite` under the working directory). The manifest parse cache and the drift baseline live next to it. |
| `latest_only` | Keep only the newest version of each list; explicitly named files survive the filter (see below). |
| `wabbajack` | `.wabbajack` files (pinned) or directories searched recursively for them (implicit). |
| `nolvus` | InstallPackage `.xml`/`.xml.gz` files (pinned) or directories of them (implicit). The keyword `'bundled'` stands for the manifests shipped inside the app plus any fetched by `update-manifests`. |
| `installs` | MO2 installs whose `[NoDelete]`-prefixed mods name archives to protect. An entry may be one install (the folder containing `mods/`) or a parent folder searched a few levels deep - nested layouts like Nolvus (`Instances\<name>\MODS`) are found. Installs without `[NoDelete]` mods contribute nothing and are dropped. |
| `recovery` | Installs whitelisted whole, by archive **name** only - a fallback for lists whose `.wabbajack` is gone. Weaker than a real manifest; prefer a snapshot. |
| `snapshots` | Snapshot JSONs exported by `modsweep snapshot`; always pinned. |
| `exclude` | Case-insensitive globs matched against the list label (`'LoreRim 2.2*'`) or the manifest file name. Retires a list without touching any files. |
| `[quarantine] dir` | Where sweep batches land (default: `_quarantine` next to the downloads dir). Must not be inside the downloads dir; keep it on the same volume so moves are instant renames. |
| `[quarantine] keep_days` | Trust period for the age-based `purge` (default 30). Governs only the CLI purge - the GUI's Purge button deletes exactly the batch you pick. |

CLI flags override the config: `--downloads`, `--cache`, `--quarantine`,
`--latest-only`, and `--exclude` (additive - it extends the config's list).
Passing `-m`/`--mo2-all` replaces the config's source set entirely for that
run, which is handy for what-if experiments.

## Commands

All commands accept `--log-level debug|info|warning|error` for diagnostics
on stderr (timings, per-source parse detail). Source-resolution
announcements (exclusions, supersessions, pins) always print to stderr -
no silent decisions.

### report

```powershell
modsweep report [--csv detail.csv]
```

Read-only inventory: classifies every file against the active sources and
prints the active-source list, a status summary, per-source claim counts,
and the largest candidates. Never computes hashes - it only reads the
cache, so a cold run is stat-speed. `--csv` writes per-file detail
(`rel_path, status, size_bytes, claimed_by, note`).

The statuses:

| Status | Meaning |
|---|---|
| `keep-verified` | Hash matches an active source - protected. |
| `keep` | Name and size match (hash not yet computed), or a name-only source such as `[NoDelete]` claims it - protected. |
| `stale-version` | An active source knows this file name, but not this exact file - a superseded or re-uploaded version. Candidate. |
| `unclaimed` | No active source references the file at all. Candidate. |
| `meta-orphan` | A `.meta` sidecar whose archive is gone. Candidate. |

In the claims table, `unique` counts files claimed by no other source:
what retiring that source would free.

### hash

```powershell
modsweep hash --only-candidates   # the pre-sweep gate (~1 min typical)
modsweep hash                     # full pass (optional; resumable)
modsweep hash --limit 500         # stop after 500 files
```

Computes xxHash64 and CRC32 in one streaming read per file and caches the
results. `--only-candidates` hashes just the current stale/unclaimed
files - all a sweep needs. Interrupt freely: progress is cached, rerun to
resume. The cache invalidates automatically when a file's size or mtime
changes, so a modified file counts as unhashed again until re-checked.

### sweep

```powershell
modsweep sweep                    # dry run: prints the plan, moves nothing
modsweep sweep --apply            # move candidates to a quarantine batch
modsweep sweep --apply --delete   # ...and purge the batch immediately - NO UNDO
```

Moves eligible candidates (stale/unclaimed/orphan-`.meta`) into a new
timestamped batch under the quarantine dir, preserving relative paths.
Safety rules, all non-negotiable:

- A candidate whose hash was never checked is **refused** (run
  `hash --only-candidates` first). The refusal and its total size are
  reported in the plan.
- A `.meta` sidecar moves only together with its archive - and inherits
  its refusal.
- The batch's `sweep-manifest.csv` (`rel_path, original_path, status,
  size_bytes, claimed_by, note`) is written incrementally as files move,
  so even a crash mid-sweep leaves a complete restore record.

`--delete` composes sweep with an immediate purge for users who trust the
cleanup and want the space back now: every safety rule above still
applies, but there is no undo. The default remains quarantine + trust
period.

### restore

```powershell
modsweep restore "F:\Modlists\Skyrim\_quarantine\2026-07-06_071750"
```

Moves a batch back to its original locations using the batch manifest.
Files whose original path is now occupied are skipped (left in
quarantine) and counted in the summary. A fully restored batch cleans up
after itself - emptied category folders and the husk batch dir are
removed.

### purge

```powershell
modsweep purge                    # dry run: verdict per batch
modsweep purge --apply            # delete batches older than keep_days
modsweep purge --older-than 7 --apply
```

Permanently deletes quarantine batches older than the trust period
(`keep_days`, default 30). The only hard-delete path in the tool: dry run
by default, and only directories carrying a `sweep-manifest.csv` are ever
considered - anything else in the quarantine dir is never touched.

### snapshot

```powershell
modsweep snapshot [--out snapshots]
```

Exports each active source as a compact JSON whitelist (name, size,
hashes, subdir per entry). A snapshot classifies identically to the
manifest it came from and survives that manifest's deletion - cheap
insurance before uninstalling Wabbajack. Snapshots load like any other
source (the `snapshots` config key, or `-m file.json`).

### update-manifests / check-update

```powershell
modsweep update-manifests   # fetch newly published bundled manifests
modsweep check-update       # is a newer release on GitHub?
```

`update-manifests` downloads bundled-manifest additions (new Nolvus guide
versions) from the project repository into a per-user data dir
(`%LOCALAPPDATA%\modsweep\manifests\nolvus` on Windows,
`~/.local/share/modsweep/...` on Linux, `~/Library/Application Support`
on macOS) - the `'bundled'` config entry picks them up automatically, no
new executable required. `check-update` compares the running version with
the latest GitHub release and prints a link; it never self-replaces the
executable.

### Ad-hoc sources: -m and --mo2-all

```powershell
modsweep report -m "C:\lists\LoreRim_@@_LoreRim.wabbajack" -m "D:\Modlists\Skyrim\LoreRim"
modsweep report --mo2-all "D:\Modlists\Skyrim\OldList"
```

`-m` infers the source type per argument: a `.wabbajack`, a bare
`modlist.json`, an InstallPackage `.xml`/`.xml.gz`, a snapshot JSON, an
MO2 install, or a directory of any of these. `--mo2-all` whitelists an
entire install by archive name (every mod's `installationFile`), the
recovery mode for lists whose manifest is gone. Ad-hoc runs replace the
config's sources and neither check nor update the drift baseline, so
experiments cannot clobber it.

## Source resolution

Every command resolves its active sources through the same pipeline. The
precedence is: **exclude > pin (explicit entry) > latest_only > active by
default** - and every decision is announced on stderr.

1. **Discovery.** Config keys (or CLI `-m`) expand to concrete manifests.
   Directory entries are walked: a `wabbajack` dir is searched recursively
   for `.wabbajack`; an `installs` entry is either an MO2 install or a
   folder searched a few levels deep for installs. Provenance is recorded
   here - a file or install you named yourself is *explicit* (pinned);
   anything found by a directory walk is *implicit*.
2. **Exclusion.** `exclude` globs match case-insensitively against the
   manifest file name (before parsing) and the list label (after). An
   excluded manifest takes no further part - exclusion beats pinning.
   Announced as `excluded (<pattern>)`.
3. **Dedupe.** Identical labels - the same list version found in several
   places - collapse to one manifest; a pin from any copy sticks. If two
   same-labeled sources differ in content (say, two same-named MO2
   installs on different drives), the drop is announced instead of silent.
4. **Empty installs.** Installs with no `[NoDelete]` entries contribute
   nothing and are dropped from evaluation.
5. **Version filter** (only when `latest_only`). Manifests group by list
   identity - the Wabbajack machine id when known (robust to a list being
   renamed between releases), else the list name. The highest version per
   group survives (numeric-aware compare: `10.0` beats `9.9`; suffixes
   like `3b` sort right after `3`; empty versions sort lowest). Pinned
   manifests are never dropped, but they still *compete* as versions, so
   pinning the newest does not resurrect older ones. Versionless sources
   (`[NoDelete]` instances) always survive. Announced as
   `superseded by <winner>` and `pinned (explicit entry) despite <winner>`.

Example - "latest of everything, except keep old LoreRim too, and retire
NGVO entirely":

```toml
latest_only = true
exclude = ['NGVO*']
wabbajack = [
    'C:\Games\Wabbajack',                    # implicit: latest wins
    'C:\lists\LoreRim_@@_LoreRim.wabbajack', # explicit: pinned
]
```

### Drift detection

Config-driven runs of `report` and `sweep` record the active set in
`.modsweep/state.json`. When a previously-active manifest can no longer be
found on disk (e.g. Wabbajack was uninstalled, taking its
`downloaded_mod_lists` along), the next run warns -
`previously-active source vanished: <label>` - instead of silently
treating its archives as unclaimed. The warning fires once; the baseline
then accepts the new set.

## Retiring a list

The report header names each active source (label + version + origin
path); that list is the thing to review. To retire one:

- add an `exclude` glob (config, `--exclude`, or untick it in the GUI) -
  the manifest stays on disk for painless reinstatement; or
- set `latest_only = true` to retire all superseded versions at once,
  pinning any old version you want to keep by naming its file; or
- delete/move the manifest out of the searched directory.

Then check the report's per-source `unique` column (that is what retiring
frees), preview with `sweep` (dry run), and `sweep --apply` when
satisfied. Keep the `.wabbajack` or a snapshot so reinstating later is a
one-line change.

## Hashing policy

Classification is name/size by default; hashing is opt-in and cached.
Hashing exists for one reason: Wabbajack identifies archives by hash and
sometimes renames them on disk, so name-only matching produces false
deletion candidates (27.6 GB worth on the reference disk). Hence the
gate: sweeps refuse any file whose hash was never checked, and
`hash --only-candidates` checks exactly the files a sweep would touch.

Rescues are conservative in both directions: an xxHash64 hit counts
outright (64-bit collisions are fanciful), while a CRC32 hit must also
agree on file size (32 bits do collide at this scale, and a rename never
changes size).

The cache (`.modsweep/hashes.sqlite`) is keyed by absolute path and
invalidated on size/mtime change. It is safe to share between the CLI and
the GUI, and safe to delete - hashes are simply recomputed.

## Layout conventions and manifest formats

Verified against real installations:

- Wabbajack lists dump all archives into the downloads **root**.
- Nolvus places mods into **category subdirectories** (`1.1 SKSE
  PLUGINS`, ...) matching `<Category><Name>` in its manifest; only its
  shared tools (Mod Organizer, SSEEdit, BSArch) land in the root.
- `.meta` files are sidecars; they follow the fate of the archive next to
  them.
- Subdirectory mismatches are reported as notes, never used to disqualify
  a match (Nolvus renumbers categories between guide versions).

| | Wabbajack (`modlist.json` inside `.wabbajack` zip) | Nolvus (`InstallPackage.xml`) |
|---|---|---|
| Size | exact bytes | `round(bytes / 1024)` - sanity check only |
| Hash | xxHash64, base64 of the little-endian digest | CRC32 (hex) - authoritative |
| Location | root | `Softwares` to root, `Categories` to subdir |

## Sources of truth, ranked

- `.wabbajack` / `modlist.json` is the only *complete* truth (name + size
  + hash). Keep the files - at 25-70 MB they are tiny next to the archives
  they describe. **Protection lasts only while the manifest is
  discoverable**: uninstalling Wabbajack or deleting the files implicitly
  retires those list versions. Drift detection warns once when this
  happens but cannot bring the whitelist back - snapshot first.
- A snapshot (`modsweep snapshot`) is a faithful, durable copy of a
  manifest's whitelist - the recommended insurance.
- An installation alone still yields a *name-level* whitelist via each
  mod's `meta.ini` (`installationFile=`); use `recovery` / `--mo2-all`
  when nothing better exists.
- Wabbajack's `GlobalHashCache2.sqlite` maps local paths to hashes but
  knows nothing about list membership - a cache, not a manifest.
