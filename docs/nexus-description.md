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

**Install.** Download the zip, extract anywhere, run `modsweep-gui.exe`.
Windows SmartScreen may warn on first run - the executables are unsigned
open-source builds; "More info > Run anyway". Linux/macOS builds, the
Python package (`pipx install "modsweep[gui]"`), and full source live on
GitHub.

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
