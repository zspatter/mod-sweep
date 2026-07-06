"""User-facing help and tooltip texts shared across the GUI."""

WELCOME = """\
Typical flow:

1. Report - classify the downloads directory (read-only)
2. Hash Candidates - verify candidates by hash (the safety gate for sweeping)
3. Sweep (Dry Run) - preview exactly what would be quarantined
4. Sweep + Apply - move candidates to a restorable quarantine batch

Sweeps never hard-delete: batches sit in quarantine until you Restore or
Purge them. Purge is the only permanent deletion. Hover any button for
details."""

TOOLTIPS = {
    "open": "Choose a different modsweep.toml",
    "edit": "Edit the config in a dialog: downloads folder, sources of "
    "truth, exclusions, and quarantine settings",
    "refresh": "Reload the active sources from the config "
    "(resolution announcements appear in the Log tab)",
    "report": "Classify every file in the downloads directory against the "
    "active sources - read-only",
    "hash": "Hash-check the current deletion candidates; sweeps refuse "
    "files whose hash was never checked",
    "dry": "Preview exactly what a sweep would quarantine - nothing is moved",
    "apply": "Move all candidates to a timestamped quarantine batch "
    "(undo with Restore)",
    "restore": "Move a quarantined batch back into the downloads directory",
    "purge": "PERMANENTLY delete a quarantine batch of YOUR choosing, any "
    "age - the keep_days trust period only guides the CLI's age-based "
    "purge, not this button",
    "snapshot": "Export each active source as a compact whitelist that "
    "survives deletion of the original manifest - cheap insurance before "
    "uninstalling Wabbajack",
}


RESOLUTION_HELP = """\
How sources become active (precedence: exclude > pin > latest-only > active):

- Everything found is active by default. Forgetting a list keeps its files \
- only explicit action exposes files for sweeping.
- Folder entries are walked and load implicitly; files you name yourself \
are PINNED: the latest-only filter never drops them.
- Excludes retire a list without touching any of its files.
- latest_only keeps only the newest version of each list (by list name); \
pinned files still count as versions, so pinning the newest does not \
resurrect older ones.

Retiring a list:
1. Untick it under Active sources (writes an exclude for you), add an \
exclude glob in the editor, or remove its manifest file.
2. Run Report - its uniquely-claimed archives become candidates.
3. Sweep when ready. Keep the .wabbajack (or a snapshot) so reinstating \
later is painless."""

STATUS_HELP = {
    "keep-verified": "Hash matches an active source - protected",
    "keep": "Name and size match an active source (or a name-only source "
    "such as [NoDelete] claims it) - protected",
    "stale-version": "An active source knows this file name, but not this "
    "exact file's hash - a superseded or re-uploaded version",
    "unclaimed": "No active source references this file at all",
    "meta-orphan": "A .meta sidecar whose archive is gone",
}
