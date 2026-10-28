from bridge import config, materials


def test_real_mbl_folder():
    m = materials.scan(config.MATERIALS_DIR / "MBL")
    assert sorted(w for k, w in m if k == "K") == [9, 10, 11, 12, 13, 14, 15]
    assert sorted(w for k, w in m if k == "R") == [9, 10, 11, 12, 13, 14, 15]
    assert m[("R", 11)].name.startswith("Slide Responsi 11Solusi")
    assert "_revisi_" in m[("K", 15)].name
    assert "_revisi_" not in m[("R", 15)].name


def test_synthetic(tmp_path):
    for n in ["slide kuliah 2 a.pdf", "Slide Kuliah 2 a_revisi_.pdf", "Slide Kuliah 3 x.PDF",
              "Slide Kuliah 4.txt", "random.pdf", "Slide Responsi7x.pdf"]:
        (tmp_path / n).write_bytes(b"")
    m = materials.scan(tmp_path)
    assert set(m) == {("K", 2), ("K", 3), ("R", 7)}
    assert m[("K", 2)].name == "Slide Kuliah 2 a_revisi_.pdf"


def test_missing_folder(tmp_path):
    assert materials.scan(tmp_path / "nope") == {}


def test_for_course_and_weeks(conn):
    assert materials.for_course(conn, "BIO2101") == {}
    assert materials.weeks(conn, "BIO2101") == {"K": [], "R": []}
    w = materials.weeks(conn, "MAT2111")
    assert w["K"][0] == 9 and w["R"][-1] == 15
    assert len(materials.for_course(conn, "MAT2111")) == 14
