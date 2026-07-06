# Mod Sweep - NexusMods page copy

Master copy for the NexusMods description. Nexus renders **BBCode, not
Markdown** - paste from the mirrored `nexus-description.bbcode` instead,
and keep both files in sync when editing (this one stays the readable
master).

---

## Short description (summary field)

Safely clean the shared downloads folder your Wabbajack lists and Nolvus
grew over the years. Whitelist-driven: every archive an installed list
still needs is protected; only true leftovers are swept - into a
restorable quarantine, never straight to deletion.

## Description

**The problem.** Wabbajack lists share one downloads directory, Nolvus
adds its own category folders to it, and every list update leaves the old
archives behind. After a few lists and a few years, that folder is a
terabyte of mixed treasure and dead weight that nobody dares to clean by
hand.

**What Mod Sweep does.** It reads the actual sources of truth - your
`.wabbajack` files, the bundled Nolvus install manifests, and the
`[NoDelete]` custom additions inside your MO2 installs - and classifies
every file in the downloads folder against them. Anything a list still
needs is kept. What remains is shown to you, hash-verified, and only then
swept into a quarantine folder you can restore from with one click.

**Safety is the design, not a feature:**
- Nothing is ever deleted directly: sweeps move files to a restorable,
  timestamped quarantine. Purge - the only permanent delete - is a
  separate, loudly-confirmed step.
- Files must pass a hash check (xxHash64/CRC32, matching what Wabbajack
  and Nolvus verify at install time) before they can be swept. A renamed
  archive that a list still needs is recognized by its hash, not its name.
- Forgetting a list keeps its files. Retiring one is an explicit action -
  untick it in the GUI - and fully reversible.

**Highlights:**
- GUI and CLI over the same engine; the GUI needs no manual config file
  editing at all.
- Handles multiple versions of the same list: keep only the newest
  (grouped correctly even across list renames), or pin specific versions.
- Nolvus install manifests ship with the app and update in-app from the
  project repository - please do not ask the Nolvus author for
  InstallPackage files.
- Per-file actions, sortable reports, snapshot exports that keep your
  whitelist working even after a `.wabbajack` is deleted.
- Checks for new releases itself (Help > Check for Updates).

**Install.** Download the zip, extract anywhere, run `modsweep-gui.exe`
from the extracted folder.
Windows SmartScreen may warn on first run - the executables are unsigned
open-source builds; "More info > Run anyway". Linux/macOS builds, the
Python package (`pipx install "modsweep[gui]"`), and full source live on
GitHub.

**Quick start.**
1. Click **Edit Config...** and point Mod Sweep at your setup: the shared
   downloads folder, your Wabbajack folder (searched for every downloaded
   list automatically), and your MO2 install folders (for `[NoDelete]`
   custom additions). The Nolvus manifests are already there as
   `bundled`. Pick a quarantine folder on the same drive as the
   downloads. No config file editing - the dialog writes it for you.
2. Click **Report**. Read-only, takes seconds: every file is classified
   against your lists, and the headline shows how much space the
   leftovers hold.
3. Click **Hash Candidates**. This verifies the leftovers by checksum -
   the same hashes Wabbajack and Nolvus use - so a renamed archive a list
   still needs can never be mistaken for junk. Sweeps refuse unverified
   files, no exceptions.
4. Click **Sweep (Dry Run)** to preview the exact file list, then
   **Sweep + Apply** to move it into a timestamped quarantine batch.
5. Changed your mind? **Restore...** puts a whole batch back exactly
   where it was. Confident? **Purge...** deletes a batch permanently
   (the only permanent action, behind a deliberately scary confirmation),
   or just let batches age out.

**Reading the report.**
- *Keep (hash verified)* and *Keep (name+size match)* - an active list
  still needs this file. Protected; sweeps never touch it.
- *Stale version* - a list knows this file name but not this exact file:
  an old download superseded by an update. Candidate.
- *Unclaimed* - no active list references it at all. Candidate.
- *Orphan .meta* - a leftover sidecar whose archive is already gone.
  Candidate.
The claims table shows what each list protects; its **unique** column is
the payoff number - how many files (and gigabytes) only that list still
holds onto, i.e. what retiring it would free. Right-click any candidate
to reveal it in Explorer, quarantine just that file, or delete it.

**Retiring old lists.** Every list found is protected by default -
forgetting one keeps its files, always. To let go of one, untick it in
the sources panel (fully reversible: tick it again to reinstate), or turn
on *latest only* in the config editor to keep just the newest version of
every list automatically. Old versions you want to keep can be pinned
with a right-click. Run Report again and watch the reclaim number grow.

**Command line included.** The same engine ships as `modsweep.exe` for
scripting and scheduled cleanups: `report` (with CSV export), `hash`,
`sweep` (dry run by default), `restore`, `purge`, `snapshot` (export
durable whitelists that survive deleting the original `.wabbajack`),
`update-manifests`, and `check-update`. Full documentation on GitHub.

**Good to know.**
- The config lives in `modsweep.toml` next to where you run the app; the
  hash cache and state live in a `.modsweep` folder. Both are plain
  files - nothing touches the registry, and nothing phones home (update
  checks only contact GitHub when you ask).
- New Nolvus guide versions arrive via Tools > Update Nolvus Manifests -
  no new download of the app required.
- Before uninstalling Wabbajack, run Snapshot: protection lasts only
  while a list's manifest exists, and snapshots are durable copies.

**Links.** Source, issues, and other platforms:
https://github.com/zspatter/mod-sweep - MIT licensed.

**Shout out.** Special thank you to
[vektor9999](https://www.nexusmods.com/profile/vektor9999) for sharing the
InstallPackages required for this project.

## Suggested Nexus fields

- Category: Utilities
- Tags: cleanup, Wabbajack, Nolvus, Mod Organizer 2
- Files: upload `modsweep-vX.Y.Z-windows.zip` from the GitHub release;
  name the file entry "Mod Sweep (Windows)" and keep the version in sync.
