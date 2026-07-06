# mod-sweep

[![CI](https://github.com/zspatter/mod-sweep/actions/workflows/ci.yml/badge.svg)](https://github.com/zspatter/mod-sweep/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/modsweep)](https://pypi.org/project/modsweep/)

![The report view: checkable source tree and classification tables](https://raw.githubusercontent.com/zspatter/mod-sweep/main/docs/screenshots/report.png)

Whitelist-driven cleanup for a Skyrim archive directory shared by multiple
modlists (Wabbajack lists + Nolvus). Mod Sweep builds a union whitelist
from your modlist manifests, classifies every file in the downloads
directory against it, and sweeps only the true leftovers. **Nothing is
deleted outright: sweeps move candidates to a restorable quarantine, and
only `purge` (or an explicit `sweep --apply --delete`) is permanent.**

## Installation

- **Standalone executables** (no Python required): grab the archive for
  your platform from the [latest release](https://github.com/zspatter/mod-sweep/releases/latest) -
  each contains the `modsweep` CLI and the `modsweep-gui` app.
- **PyPI**: `uv tool install "modsweep[gui]"` (or
  `pipx install "modsweep[gui]"`) installs both commands; drop the `[gui]`
  extra for the CLI only. Upgrade later with `uv tool upgrade modsweep`.
- **From source**: clone, `uv sync --extra gui`, then `uv run modsweep` /
  `uv run modsweep-gui`.

## Quick start

`modsweep.toml` declares the downloads dir, the active sources of truth,
and the quarantine dir - with it in place the commands need no arguments.
Start from [modsweep.example.toml](modsweep.example.toml), or let the
GUI's Edit Config dialog write the file for you (new users never need to
touch TOML). Then:

```powershell
modsweep report                      # read-only inventory: what is protected, what is left over
modsweep hash --only-candidates      # hash-check the leftovers (the pre-sweep safety gate)
modsweep sweep                       # dry run: preview exactly what would be quarantined
modsweep sweep --apply               # move candidates to a restorable quarantine batch
modsweep restore <batch-dir>         # change your mind
modsweep purge                       # age out old batches (dry run; --apply to act)
```

(Running from source, prefix with `uv run`.) Every command takes
`--log-level debug|info` for diagnostics, and `modsweep <cmd> --help`
documents its flags.

## How it stays safe

- **Forgetting a list keeps its files.** Every manifest found is active by
  default; only explicit action (an exclude, an old version losing under
  `latest_only`) exposes files for sweeping - and every resolution
  decision is announced, never silent.
- **The hash gate.** Wabbajack renames archives on disk, so name-only
  matching produces false candidates. Sweeps refuse any file whose hash
  was never verified against the whitelist; renamed archives a list still
  needs are rescued by hash.
- **Quarantine first.** Sweeps move files into timestamped batches with a
  restore manifest; `restore` puts a batch back untouched. Purging is a
  separate, dry-run-by-default step with a trust period.

## Documentation

- [Usage guide](https://github.com/zspatter/mod-sweep/blob/main/docs/usage.md) -
  every command and config key in depth: source resolution and retirement,
  the hashing policy, quarantine lifecycle, snapshots, drift detection,
  manifest formats.
- [GUI tour](https://github.com/zspatter/mod-sweep/blob/main/docs/gui.md) -
  the desktop app: source tree states, report tables, config editor,
  per-file actions, restore/purge semantics.
- [Maintaining](https://github.com/zspatter/mod-sweep/blob/main/docs/MAINTAINING.md) -
  release procedure, manifest bumps, CI notes.

## Bundled Nolvus manifests

Nolvus `InstallPackage.xml` files are not distributed publicly, so this
project bundles them (gzipped) as package data - **please do not contact
the Nolvus author for these files**; new guide releases are contributed to
this project instead. The `bundled` entry in the `nolvus` config key
resolves to the shipped manifests plus in-app updates
(`modsweep update-manifests` / Tools > Update Nolvus Manifests), fetched
straight from this repository - no new executable required for a manifest
bump.

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

## Development

`uv run pytest` runs the suite (~96% coverage with `--cov=modsweep`): unit
tests per module, property-based tests for version ordering, GUI smoke
tests (offscreen), and end-to-end tests that drive the CLI through the
full lifecycle - classification, hash-gate refusal, hashing, sweep,
restore, and purge aging. `uvx ruff check` and `uv run pyright` must stay
clean; CI enforces both and runs the tests on Windows/Linux/macOS plus
Debian and Arch containers, on Python 3.12, 3.14, and latest.

## Acknowledgements

Special thank you to [vektor9999](https://github.com/vektor9999) for
sharing the Nolvus InstallPackages this project bundles.

## License

MIT - do what you like with it, keep the attribution (the copyright notice
in [LICENSE](LICENSE)).

## Roadmap

- App self-update beyond notify-and-link (exe self-replacement) if users
  ask for it; package-manager installs already upgrade via uv/pipx.
- Performance note (settled): parsed manifests are cached under
  `.modsweep/manifest_cache` keyed by source size/mtime (12.5s to 0.9s
  resolution on the reference setup). File-level parallel hashing is
  deliberately skipped - hashing is drive-bound on HDDs - though reads
  and hashing are pipelined within each file.
- Nolvus sibling list: the author's next guide is expected to use the same
  InstallPackage format - bundle its manifests as they are released.
