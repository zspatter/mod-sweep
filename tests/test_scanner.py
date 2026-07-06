from modsweep.scanner import scan


def test_scan_rel_paths_use_forward_slashes(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "root.7z").write_bytes(b"r")
    (tmp_path / "a" / "one.7z").write_bytes(b"1")
    (tmp_path / "a" / "b" / "two.7z").write_bytes(b"2")

    by_name = {f.name: f for f in scan(tmp_path)}
    assert by_name["root.7z"].rel == "root.7z"
    assert by_name["root.7z"].subdir == ""
    assert by_name["one.7z"].rel == "a/one.7z"
    assert by_name["one.7z"].subdir == "a"
    assert by_name["two.7z"].rel == "a/b/two.7z"
    assert by_name["two.7z"].subdir == "a/b"


def test_meta_detection_and_base_rel(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "mod.7z").write_bytes(b"m")
    (tmp_path / "sub" / "mod.7z.META").write_text("x", encoding="utf-8")

    by_name = {f.name: f for f in scan(tmp_path)}
    meta = by_name["mod.7z.META"]
    assert meta.is_meta  # extension check is case-insensitive
    assert meta.base_rel == "sub/mod.7z"
    assert not by_name["mod.7z"].is_meta


def test_scan_records_size_and_mtime(tmp_path):
    (tmp_path / "a.7z").write_bytes(b"12345")
    (disk,) = scan(tmp_path)
    assert disk.size == 5
    assert disk.mtime_ns > 0
    assert disk.path == tmp_path / "a.7z"
