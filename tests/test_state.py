import json
import zipfile
from pathlib import Path

from modsweep import state
from modsweep.cli import main
from modsweep.manifest import Manifest


def man(label, source):
    return Manifest(label=label, source_path=Path(source))


def test_read_missing_or_corrupt_is_empty(tmp_path):
    assert state.read(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert state.read(bad) == {}


def test_write_read_roundtrip(tmp_path):
    p = tmp_path / "sub" / "state.json"
    state.write(p, [man("L 1.0", tmp_path / "l.wabbajack")])
    assert state.read(p) == {"L 1.0": str(tmp_path / "l.wabbajack")}


def test_vanished_only_when_source_file_is_gone(tmp_path):
    existing = tmp_path / "still-here.wabbajack"
    existing.write_bytes(b"x")
    previous = {
        "Gone 1.0": str(tmp_path / "deleted.wabbajack"),  # file gone -> warn
        "Excluded 1.0": str(existing),  # file exists -> dropped on purpose
        "Active 1.0": str(tmp_path / "whatever"),  # still active -> fine
    }
    current = [man("Active 1.0", tmp_path / "whatever")]
    assert state.vanished(previous, current) == [
        ("Gone 1.0", str(tmp_path / "deleted.wabbajack"))
    ]


# --- end-to-end through main() ---------------------------------------------


def e2e_config(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    wj_dir = tmp_path / "lists"
    wj_dir.mkdir()
    for name, version in (("a", "1.0"), ("b", "1.0")):
        with zipfile.ZipFile(wj_dir / f"{name}.wabbajack", "w") as zf:
            zf.writestr(
                "modlist",
                json.dumps({"Name": name.upper(), "Version": version, "Archives": []}),
            )
    cfg = tmp_path / "modsweep.toml"
    cfg.write_text(
        f"""
downloads = '{dl}'
cache = '{tmp_path / ".modsweep" / "hashes.sqlite"}'
wabbajack = ['{wj_dir}']
""",
        encoding="utf-8",
    )
    return dl, wj_dir, cfg


def test_report_warns_when_active_source_vanishes(tmp_path, capsys):
    _, wj_dir, cfg = e2e_config(tmp_path)

    assert main(["report", "--config", str(cfg)]) == 0
    assert "vanished" not in capsys.readouterr().err

    (wj_dir / "b.wabbajack").unlink()
    assert main(["report", "--config", str(cfg)]) == 0
    err = capsys.readouterr().err
    assert "previously-active source vanished: B 1.0" in err

    # Once acknowledged (baseline updated), the warning does not repeat.
    assert main(["report", "--config", str(cfg)]) == 0
    assert "vanished" not in capsys.readouterr().err


def test_adhoc_manifest_runs_do_not_touch_baseline(tmp_path, capsys):
    dl, wj_dir, cfg = e2e_config(tmp_path)
    assert main(["report", "--config", str(cfg)]) == 0
    capsys.readouterr()

    # Ad-hoc -m subset: no drift check, no baseline clobbering.
    assert main(
        [
            "report", "--config", str(cfg),
            "--downloads", str(dl),
            "-m", str(wj_dir / "a.wabbajack"),
            "--cache", str(tmp_path / ".modsweep" / "hashes.sqlite"),
        ]
    ) == 0
    assert "vanished" not in capsys.readouterr().err

    state_file = tmp_path / ".modsweep" / "state.json"
    assert set(state.read(state_file)) == {"A 1.0", "B 1.0"}


def test_read_rejects_non_dict_active_payload(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"active": ["a label"]}', encoding="utf-8")
    assert state.read(path) == {}
