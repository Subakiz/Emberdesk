import re
from pathlib import Path

from . import config

# "Slide Responsi 11Solusi ..." has no space after the number, hence \s*.
_NAME = re.compile(r"Slide\s+(Kuliah|Responsi)\s*(\d+)", re.I)


def scan(folder: Path) -> dict[tuple[str, int], Path]:
    found: dict[tuple[str, int], Path] = {}
    if not folder.is_dir():
        return found
    # sorted so the result is deterministic if two files tie
    for p in sorted(folder.iterdir()):
        m = _NAME.search(p.name)
        if p.suffix.lower() != ".pdf" or not m:
            continue
        key = (m[1][0].upper(), int(m[2]))
        # a _revisi_ file replaces the plain one, never the other way round
        if key not in found or "_revisi_" in p.name.lower():
            found[key] = p
    return found


def for_course(conn, course_code: str) -> dict[tuple[str, int], Path]:
    row = conn.execute("SELECT folder FROM courses WHERE code=?", (course_code,)).fetchone()
    if not row or not row["folder"]:
        return {}
    return scan(config.MATERIALS_DIR / row["folder"])


def weeks(conn, course_code: str) -> dict[str, list[int]]:
    mats = for_course(conn, course_code)
    return {k: sorted(w for kind, w in mats if kind == k) for k in ("K", "R")}
