# Maintaining Mod Sweep

The procedures that keep the project shipping. Everything here assumes a
clone with `uv sync --extra gui` done.

## Cutting a release

1. Bump the version in **both** places: `pyproject.toml` (`version`) and
   `src/modsweep/__init__.py` (`__version__`). Run `uv lock` to refresh the
   lockfile, run `uv run pytest`, commit.
2. Tag and push:
   ```
   git tag -a vX.Y.Z -m "release notes"
   git push origin main vX.Y.Z
   ```
3. The tag triggers `.github/workflows/release.yml`:
   - builds sdist + wheel and publishes to PyPI via **trusted publishing**
     (registered publisher: repo `zspatter/mod-sweep`, workflow
     `release.yml`, environment `pypi` - no tokens anywhere);
   - builds the console `modsweep` + windowed `modsweep-gui` executables on
     Windows/Linux/macOS, smoke-tests each frozen CLI (including bundled
     manifest resolution), and attaches the archives to the GitHub release.
4. Verify: the PyPI page shows the new version; `uv tool upgrade modsweep`
   works; release assets download. Then update NexusMods (below).

PyPI metadata (author, description, README) is immutable per release -
fixes require a new version.

## Bumping the Nolvus manifests

New guide versions are contributed to this repo, never requested from the
Nolvus author.

1. Gzip the InstallPackage.xml:
   `python -c "import gzip,shutil; shutil.copyfileobj(open('InstallPackage.xml','rb'), gzip.open('src/modsweep/data/nolvus/<guide>-<version>.xml.gz','wb',9))"`
2. Sanity-check it parses: `uv run modsweep report` should list the new
   version as an active source (or superseded, under latest-only).
3. Commit and push to `main`. **No release is required**: installed copies
   fetch new manifests directly from this repository via
   `modsweep update-manifests` / Tools > Update Nolvus Manifests (the
   GitHub contents API reads `src/modsweep/data/nolvus` on `main`).
   Packaged copies additionally bundle it at the next release.

The expected Nolvus sibling list uses the same format - same procedure,
new file name prefix.

## Updating NexusMods

- Upload the new `modsweep-vX.Y.Z-windows.zip` from the GitHub release and
  update the page version. Page copy lives in `docs/nexus-description.md`
  (readable master) with a BBCode mirror in `docs/nexus-description.bbcode` -
  Nexus renders BBCode, not Markdown, so paste the `.bbcode` file. Edit the
  master first, then update the mirror.
- New uploads may get auto-quarantined: PyInstaller onefile bootloaders
  trip AV heuristics (unsigned, new hash, self-extracting). Support review
  with the GitHub repo and CI build linked clears it; switching the build
  to onedir is the standing mitigation if it recurs.

## Assets

- **Icon**: the drawing lives in `modsweep/gui.py::_app_icon`; only the
  exported `assets/modsweep.ico` ships. The exporter
  (`packaging/make_icon.py`) is local-only tooling, deliberately
  untracked - regenerate with `uv run python packaging/make_icon.py`
  after changing the drawing.
- **Screenshots**: `uv run python packaging/make_screenshots.py [config]`
  regenerates `docs/screenshots/` offscreen (loads Windows fonts manually -
  the offscreen platform has no GDI access). Private list names to hide go
  in the gitignored `packaging/screenshots-omit.txt` (one substring per
  line); rows are removed from the rendered widgets only, so nothing leaks
  through the candidates table. README embeds the shots via
  raw.githubusercontent URLs so PyPI renders them too.

## CI notes

- `ci.yml` runs the suite on Windows/Linux/macOS (Python 3.12/3.14/latest)
  plus Debian and Arch containers, on every push/PR and weekly.
- The weekly run also executes live GitHub API contract tests
  (`tests/test_live_remote.py`, gated by `MODSWEEP_LIVE_TESTS=1`) and a
  keepalive job that resets GitHub's 60-day cron pause.
- `git-filter-repo` crashes on this machine's multiline git aliases
  (`~/.gitalias`); run it with `GIT_CONFIG_GLOBAL=NUL` set if ever needed.
