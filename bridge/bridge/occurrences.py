import re
from datetime import date as Date, datetime

from . import config, materials


class Conflict(Exception):
    pass


def _start_dt(date: str, start: str) -> datetime:
    return datetime.fromisoformat(f"{date}T{start}").replace(tzinfo=config.TZ)


def materialize(conn, date: str) -> None:
    wd = Date.fromisoformat(date).weekday()
    conn.execute(
        """INSERT OR IGNORE INTO occurrences(session_id, course_code, kind, date, start, end, room)
           SELECT id, course_code, kind, ?, start, end, room FROM sessions WHERE weekday=?""",
        (date, wd),
    )
    conn.commit()


def _occ(conn, row, now=None) -> dict:
    now = now or config.now()
    course = conn.execute("SELECT name FROM courses WHERE code=?", (row["course_code"],)).fetchone()
    has = any(k == row["kind"] for k, _ in materials.for_course(conn, row["course_code"]))
    return {
        "id": row["id"],
        "session_id": row["session_id"],
        "course_code": row["course_code"],
        "course_name": course["name"],
        "kind": row["kind"],
        "date": row["date"],
        "start": row["start"],
        "end": row["end"],
        "room": row["room"],
        "status": row["status"],
        "is_pengganti": row["is_pengganti"],
        "week": row["week"],
        "briefing_md": row["briefing_md"],
        "has_materials": has,
        "can_cancel": row["status"] == "scheduled" and now < _start_dt(row["date"], row["start"]),
        "can_uncancel": row["status"] == "cancelled" and now < _start_dt(row["date"], row["start"]),
    }


def list_day(conn, date: str) -> list[dict]:
    materialize(conn, date)
    rows = conn.execute("SELECT * FROM occurrences WHERE date=? ORDER BY start, id", (date,)).fetchall()
    now = config.now()
    return [_occ(conn, r, now) for r in rows]


def get(conn, occ_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM occurrences WHERE id=?", (occ_id,)).fetchone()
    return _occ(conn, row) if row else None


def next_unbriefed_week(conn, course_code: str, kind: str) -> int | None:
    row = conn.execute("SELECT start_week FROM courses WHERE code=?", (course_code,)).fetchone()
    floor = (row["start_week"] if row else None) or 0
    taken = {r["week"] for r in conn.execute(
        "SELECT week FROM occurrences WHERE course_code=? AND kind=? AND status='scheduled' AND week IS NOT NULL",
        (course_code, kind))}
    free = [w for k, w in materials.for_course(conn, course_code) if k == kind and w >= floor and w not in taken]
    return min(free, default=None)


def _set_status(conn, occ_id, status, now):
    row = conn.execute("SELECT * FROM occurrences WHERE id=?", (occ_id,)).fetchone()
    if not row:
        raise LookupError(f"occurrence {occ_id} not found")
    if (now or config.now()) >= _start_dt(row["date"], row["start"]):
        raise Conflict("class already started")
    if status == "cancelled":
        # week=NULL returns the material week to the pool
        conn.execute("UPDATE occurrences SET status=?, week=NULL, briefing_md=NULL, briefed_at=NULL WHERE id=?",
                     (status, occ_id))
    else:
        conn.execute("UPDATE occurrences SET status=? WHERE id=?", (status, occ_id))
    conn.commit()
    return get(conn, occ_id)


def cancel(conn, occ_id: int, now=None) -> dict:
    return _set_status(conn, occ_id, "cancelled", now)


def uncancel(conn, occ_id: int, now=None) -> dict:
    return _set_status(conn, occ_id, "scheduled", now)


def create_pengganti(conn, course_code, kind, date, start, end, room=None) -> dict:
    if kind not in ("K", "R"):
        raise ValueError("kind must be K or R")
    Date.fromisoformat(date)
    for t in (start, end):
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError(f"bad time {t!r}, want HH:MM")
    if end <= start:
        raise ValueError("end must be after start")
    if not conn.execute("SELECT 1 FROM courses WHERE code=?", (course_code,)).fetchone():
        raise ValueError(f"unknown course {course_code!r}")
    cur = conn.execute(
        """INSERT INTO occurrences(session_id, course_code, kind, date, start, end, room, is_pengganti)
           VALUES(NULL,?,?,?,?,?,?,1)""",
        (course_code, kind, date, start, end, room),
    )
    conn.commit()
    return get(conn, cur.lastrowid)
