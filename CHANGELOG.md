# Changelog

Notable changes to Mod Sweep. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.2] - 2026-07-06

### Fixed

- The GUI no longer exits silently when an action hits a configuration
  error (for example a quarantine dir inside the downloads dir); failures
  now land in the status bar and Log like any other error.
- Name-only sources (`[NoDelete]` additions) are credited on hash-verified
  files, so the claims table's `unique` column truthfully shows what
  retiring a list would free.
- CRC32 rescues additionally require a size match, removing the chance of
  a random 32-bit collision protecting an unrelated file.
- Duplicate list labels with differing content (two same-named MO2
  installs) are announced instead of silently dropping the second copy.
- GUI actions refuse early with a clear message when no downloads
  directory is configured yet.

### Changed

- Reports bulk-load the hash cache: one query instead of one per file.
- The GUI's Hash Candidates hashes and refreshes the report in a single
  pass over the disk instead of two.
- Hashing pipelines reads with computation; access stays strictly
  sequential, so spinning disks see no extra seeking.
- The hash cache uses SQLite WAL mode: no per-file fsync, and concurrent
  readers no longer block the writer.
- Standalone executables are onedir bundles (one folder, both executables,
  shared runtime), which trip far fewer antivirus heuristics than the
  previous self-extracting stubs.
- Every subcommand accepts `--log-level` (`restore`, `purge`,
  `update-manifests`, and `check-update` previously lacked it).
- `hash --limit 0` now means "hash nothing" instead of "no limit".
- CSV export orders rows by the same status ranking the report displays.

### Added

- In-depth documentation: [docs/usage.md](docs/usage.md) covers every
  command and config key, [docs/gui.md](docs/gui.md) tours the desktop
  app; the README slimmed to an overview.
- A tracked [modsweep.example.toml](modsweep.example.toml) template (the
  personal `modsweep.toml` is no longer tracked).
- `py.typed`: the package advertises its inline type annotations to type
  checkers.

### Internal

- The GUI module became a package (texts / icons / workers / editor /
  window).
- ruff and pyright run in CI; the suite grew to 241 tests at 98% branch
  coverage, including property-based invariants for version ordering and
  the matcher contract, and much deeper end-to-end and GUI coverage.

## [0.2.1] - 2026-07-06

### Fixed

- Nolvus install discovery handles the real on-disk layout: instances
  live under `Instances\<name>\MODS`, where `MODS` wraps a whole portable
  MO2 container. Install entries are searched a few levels deep and
  wrapped containers are descended, surfacing `[NoDelete]` additions that
  were previously invisible.

### Added

- The README documents all three install channels; installing the GUI via
  a tool manager needs the extra (`uv tool install "modsweep[gui]"`), and
  the import error message now explains the remedy per install style.
- The release workflow smoke-tests the frozen CLIs (including
  bundled-manifest resolution) before packaging; weekly CI runs live
  GitHub API contract tests.

## [0.2.0] - 2026-07-06

First public release: PyPI package and standalone executables.

### Added

- Nolvus manifests ship as package data; the `bundled` config keyword
  resolves to the shipped manifests plus a per-user data dir.
- `update-manifests` fetches newly published manifests straight from the
  repository; `check-update` reports newer releases (notify and link).
- Release automation: PyPI trusted publishing plus Windows/Linux/macOS
  executables attached to each GitHub release.
- Application icon; MIT license.

## [0.1.0] - 2026-07-06

Initial tagged milestone: CLI and GUI complete over the whitelist-driven
pipeline - report / hash / sweep / restore / snapshot / purge, the
quarantine-first safety model, the hash gate, and source resolution with
pinning, exclusion, and latest-only filtering.

[0.2.2]: https://github.com/zspatter/mod-sweep/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/zspatter/mod-sweep/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/zspatter/mod-sweep/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/zspatter/mod-sweep/releases/tag/v0.1.0
