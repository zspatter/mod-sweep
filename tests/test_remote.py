import json

import pytest

from modsweep import bundled, remote

INDEX = json.dumps(
    [
        {"type": "file", "name": "nolvus-awakening-6.0.20.xml.gz",
         "download_url": "https://raw.example/a.xml.gz"},
        {"type": "file", "name": "nolvus-awakening-7.0.0.xml.gz",
         "download_url": "https://raw.example/b.xml.gz"},
        {"type": "file", "name": "README.md", "download_url": "https://raw.example/r"},
        {"type": "dir", "name": "sub", "download_url": None},
    ]
).encode()


def fake_get(payloads):
    def get(url):
        assert url in payloads, f"unexpected fetch: {url}"
        return payloads[url]

    return get


def test_available_manifests_filters_to_manifest_files():
    get = fake_get({remote.MANIFEST_INDEX_URL: INDEX})
    names = [r.name for r in remote.available_manifests(get)]
    assert names == [
        "nolvus-awakening-6.0.20.xml.gz",
        "nolvus-awakening-7.0.0.xml.gz",
    ]


def test_update_manifests_downloads_only_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bundled, "user_dir", lambda: tmp_path / "user")
    monkeypatch.setattr(
        bundled, "known_names", lambda: {"nolvus-awakening-6.0.20.xml.gz"}
    )
    get = fake_get(
        {
            remote.MANIFEST_INDEX_URL: INDEX,
            "https://raw.example/b.xml.gz": b"NEW MANIFEST BYTES",
        }
    )
    downloaded = remote.update_manifests(get)
    assert downloaded == ["nolvus-awakening-7.0.0.xml.gz"]
    written = tmp_path / "user" / "nolvus-awakening-7.0.0.xml.gz"
    assert written.read_bytes() == b"NEW MANIFEST BYTES"


def test_update_manifests_noop_when_current(monkeypatch):
    monkeypatch.setattr(
        bundled, "known_names",
        lambda: {"nolvus-awakening-6.0.20.xml.gz", "nolvus-awakening-7.0.0.xml.gz"},
    )
    get = fake_get({remote.MANIFEST_INDEX_URL: INDEX})
    assert remote.update_manifests(get) == []


def release(tag, url="https://github.com/x/releases/tag/v9"):
    return json.dumps({"tag_name": tag, "html_url": url}).encode()


def test_check_update_reports_newer_release():
    get = fake_get({remote.LATEST_RELEASE_URL: release("v0.2.0")})
    info = remote.check_update("0.1.0", get)
    assert info is not None
    assert (info.current, info.latest) == ("0.1.0", "0.2.0")
    assert info.url.endswith("/v9")


@pytest.mark.parametrize("tag", ["v0.1.0", "0.1.0", "v0.0.9"])
def test_check_update_quiet_when_current_or_ahead(tag):
    get = fake_get({remote.LATEST_RELEASE_URL: release(tag)})
    assert remote.check_update("0.1.0", get) is None


def test_check_update_handles_missing_tag():
    get = fake_get({remote.LATEST_RELEASE_URL: json.dumps({}).encode()})
    assert remote.check_update("0.1.0", get) is None
