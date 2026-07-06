from modsweep import nolvus
from modsweep.manifest import Entry

XML = """<?xml version="1.0" encoding="utf-8"?>
<InstallationManifest>
  <Settings><Guide><Name>Nolvus Test</Name><Version>6.0</Version></Guide></Settings>
  <Softwares>
    <Soft><Files><File>
      <FileName>tool.7z</FileName><Size>100</Size><CRC32>0000ABCD</CRC32>
    </File></Files></Soft>
  </Softwares>
  <Categories>
    <Category><Name>1.1 TEST</Name><Mods><Mod><Files><File>
      <FileName>mod.zip</FileName><Size>10</Size><CRC32>FFFFFFFF</CRC32>
    </File></Files></Mod></Mods></Category>
  </Categories>
</InstallationManifest>
"""


def test_load_tools_and_categories(tmp_path):
    p = tmp_path / "InstallPackage.xml"
    p.write_text(XML, encoding="utf-8")
    m = nolvus.load(p)
    assert m.label == "Nolvus Test 6.0"
    tool, mod = m.entries
    assert (tool.kind, tool.subdir, tool.size_kb, tool.crc32) == ("tool", "", 100, 0xABCD)
    assert (mod.kind, mod.subdir, mod.crc32) == ("mod", "1.1 TEST", 0xFFFFFFFF)


def test_load_gzipped_manifest(tmp_path):
    import gzip

    p = tmp_path / "bundled-6.0.xml.gz"
    with gzip.open(p, "wt", encoding="utf-8") as fh:
        fh.write(XML)
    m = nolvus.load(p)
    assert m.label == "Nolvus Test 6.0"
    assert len(m.entries) == 2


def test_size_kb_matching_allows_rounding_slack():
    e = Entry(file_name="x.7z", size_kb=100)
    assert e.matches_size(100 * 1024)
    assert e.matches_size(100 * 1024 + 1024)  # 1 KB of slack on top of rounding
    assert not e.matches_size(200 * 1024)


def test_exact_size_matching_is_strict():
    e = Entry(file_name="x.7z", size=1000)
    assert e.matches_size(1000)
    assert not e.matches_size(1001)


def test_file_entries_without_names_are_skipped(tmp_path):
    xml = tmp_path / "InstallPackage.xml"
    xml.write_text(
        '<?xml version="1.0"?><InstallationManifest>'
        "<Settings><Guide><Name>G</Name><Version>1.0</Version></Guide></Settings>"
        "<Softwares><Soft><Files>"
        "<File><FileName></FileName><Size>1</Size></File>"
        "<File><FileName>tool.7z</FileName><Size>1</Size><CRC32>AB</CRC32></File>"
        "</Files></Soft></Softwares><Categories/></InstallationManifest>",
        encoding="utf-8",
    )
    manifest = nolvus.load(xml)
    assert [e.file_name for e in manifest.entries] == ["tool.7z"]
