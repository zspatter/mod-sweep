"""Shared fixtures for the whole suite."""

import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    """Run every test from its own temp directory.

    Config discovery and the default cache path both look at the CWD, so a
    modsweep.toml or .modsweep/ in the developer's checkout would silently
    leak real paths into any test that does not override every value.
    """
    monkeypatch.chdir(tmp_path)
