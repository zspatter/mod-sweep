"""Live API contract tests - opt-in via MODSWEEP_LIVE_TESTS=1.

The unit suite fakes GitHub; these hit the real endpoints (weekly in CI)
to catch drift in the response fields the update paths depend on. The
publish plumbing itself (PyPI, release uploads) stays untested by design:
it is pypa/GitHub-maintained, and its one observable contract - installable
artifacts with working entry points - was verified at release time.
"""

import os

import pytest

from modsweep import remote

pytestmark = pytest.mark.skipif(
    not os.environ.get("MODSWEEP_LIVE_TESTS"),
    reason="live network tests are opt-in (set MODSWEEP_LIVE_TESTS=1)",
)


def test_manifest_index_contract():
    manifests = remote.available_manifests()
    names = [m.name for m in manifests]
    assert "nolvus-awakening-6.0.20.xml.gz" in names
    assert all(m.download_url.startswith("https://") for m in manifests)


def test_release_contract():
    info = remote.check_update("0.0.1")
    assert info is not None  # a published release exists and is newer
    assert info.latest >= "0.2.0"
    assert info.url.startswith("https://github.com/")
