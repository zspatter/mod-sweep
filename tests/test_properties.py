"""Property-based tests for version ordering and latest-only filtering.

These two pure functions decide which lists stay active - the safety of
every sweep rests on them, so they get invariants, not just examples.
"""

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from modsweep.manifest import Manifest, latest_only, version_key

# Realistic version fragments plus arbitrary junk: version_key must never
# crash on whatever a manifest happens to carry.
versions = st.one_of(
    st.text(max_size=20),
    st.lists(st.integers(min_value=0, max_value=999), min_size=1, max_size=5).map(
        lambda parts: ".".join(map(str, parts))
    ),
)


@given(versions)
def test_version_key_total_and_deterministic(version):
    key = version_key(version)
    assert isinstance(key, list)
    assert key == version_key(version)  # deterministic
    assert all(isinstance(part, tuple) and len(part) == 3 for part in key)


@given(
    st.lists(st.integers(min_value=0, max_value=999), min_size=1, max_size=5),
    st.lists(st.integers(min_value=0, max_value=999), min_size=1, max_size=5),
)
def test_numeric_versions_order_like_int_tuples(a, b):
    """'10.0' must beat '9.9': numeric components compare as numbers."""
    key_a = version_key(".".join(map(str, a)))
    key_b = version_key(".".join(map(str, b)))
    assert (key_a < key_b) == (a < b)
    assert (key_a == key_b) == (a == b)


@given(st.integers(min_value=0, max_value=999))
def test_suffixed_component_sorts_after_bare_number(n):
    assert version_key(str(n)) < version_key(f"{n}b")
    assert version_key(f"{n}b") < version_key(str(n + 1))


@given(versions)
def test_empty_version_sorts_lowest(version):
    assert version_key("") <= version_key(version)


def _manifests(entries: list[tuple[str, str]]) -> list[Manifest]:
    return [
        Manifest(label=f"{name} {version}".strip(), source_path=Path("m"),
                 name=name, version=version)
        for name, version in entries
    ]


groups = st.lists(
    st.tuples(
        st.sampled_from(["Alpha", "Beta", "Gamma"]),
        st.lists(st.integers(min_value=0, max_value=9), min_size=1, max_size=3).map(
            lambda parts: ".".join(map(str, parts))
        ),
    ),
    min_size=1,
    max_size=8,
)


@given(groups)
def test_latest_only_keeps_a_maximal_version_per_group(entries):
    manifests = _manifests(entries)
    kept, superseded, pinned_kept = latest_only(manifests)
    assert pinned_kept == []  # nothing pinned here
    assert len(kept) + len(superseded) == len(manifests)  # a full partition
    best: dict[str, list] = {}
    for m in manifests:
        key = version_key(m.version)
        best[m.group_key] = max(best.get(m.group_key, key), key)
    for m in kept:
        assert version_key(m.version) == best[m.group_key]
    for old, winner in superseded:
        assert version_key(old.version) <= version_key(winner.version)


@given(groups, st.data())
def test_latest_only_never_drops_pinned(entries, data):
    manifests = _manifests(entries)
    pinned_labels = {
        m.label for m in manifests if data.draw(st.booleans(), label=f"pin {m.label}")
    }
    kept, superseded, _ = latest_only(manifests, pinned_labels)
    kept_labels = {m.label for m in kept}
    assert pinned_labels <= kept_labels
    for old, _winner in superseded:
        assert old.label not in pinned_labels


# --- matcher invariants -------------------------------------------------------


def _disk_file(name: str, size: int):
    from modsweep.scanner import DiskFile

    return DiskFile(
        path=Path("X:/dl") / name, rel=name, subdir="", name=name,
        size=size, mtime_ns=0,
    )


class _Stub:
    def __init__(self, data):
        self.data = data

    def get(self, disk):
        return self.data.get(disk.rel)


_NAMES = ["alpha.7z", "beta.7z", "gamma.7z"]
_HASHES = ["H1=", "H2="]


def _entries():
    from modsweep.manifest import Entry

    return st.lists(
        st.builds(
            Entry,
            file_name=st.sampled_from(_NAMES),
            size=st.one_of(st.none(), st.integers(min_value=1, max_value=4)),
            xxh64_b64=st.one_of(st.none(), st.sampled_from(_HASHES)),
        ),
        max_size=6,
    )


_disk_files = st.lists(
    st.builds(
        _disk_file,
        name=st.sampled_from([*_NAMES, "junk.7z", "junk.7z.meta"]),
        size=st.integers(min_value=1, max_value=4),
    ),
    max_size=6,
    unique_by=lambda f: f.rel,
)
_hashed = st.dictionaries(
    st.sampled_from([*_NAMES, "junk.7z"]),
    st.tuples(st.sampled_from(_HASHES), st.integers(min_value=0, max_value=3)),
    max_size=4,
)


@given(_entries(), _disk_files, _hashed)
def test_matcher_invariants(entry_list, files, cache_data):
    """Whatever the whitelist and disk state, classification obeys:
    one result per file, protection implies a claimant (and vice versa),
    and sidecars always mirror their archive."""
    from modsweep.matcher import KEEP, KEEP_VERIFIED, META_ORPHAN, match

    manifests = [Manifest(label="L", source_path=Path("m"), entries=entry_list)]
    results = match(files, manifests, _Stub(cache_data))
    by_rel = {r.disk.rel: r for r in results}
    assert len(results) == len(files)
    for r in results:
        protected = r.status in (KEEP, KEEP_VERIFIED)
        assert protected == bool(r.claimed_by)
        if r.sidecar and r.status != META_ORPHAN:
            base = by_rel[r.disk.rel[: -len(".meta")]]
            assert (r.status, r.claimed_by) == (base.status, base.claimed_by)
