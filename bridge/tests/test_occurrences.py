from datetime import datetime

import pytest

from bridge import config, occurrences as oc

MON = "2026-10-26"  # Monday: BIO K 08:00, MAT K 10:00
TUE = "2026-10-27"  # MAT R 13:00


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=config.TZ)


def mat_k(conn, date=MON):
    return next(o for o in oc.list_day(conn, date) if o["course_code"] == "MAT2111")


def test_materialize_idempotent_sorted(conn):
    first = oc.list_day(conn, MON)
    assert [o["course_code"] for o in first] == ["BIO2101", "MAT2111"]
    assert [o["id"] for o in oc.list_day(conn, MON)] == [o["id"] for o in first]
    assert oc.list_day(conn, "2026-10-31") == []  # Saturday


def test_occ_keys_and_materials(conn):
    o = mat_k(conn)
    assert set(o) == {"id", "session_id", "course_code", "course_name", "kind", "date", "start", "end",
                      "room", "status", "is_pengganti", "week", "briefing_md", "has_materials", "can_cancel", "can_uncancel"}
    assert o["has_materials"] is True and o["course_name"].startswith("Matematika")
    assert oc.list_day(conn, MON)[0]["has_materials"] is False
    assert oc.get(conn, 99999) is None


def test_can_cancel_follows_clock(conn, monkeypatch):
    monkeypatch.setattr(config, "now", lambda: at("2026-10-26T09:59"))
    assert mat_k(conn)["can_cancel"] is True
    monkeypatch.setattr(config, "now", lambda: at("2026-10-26T10:00"))
    assert mat_k(conn)["can_cancel"] is False


def test_week_pool_and_cancel(conn):
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") == 9
    assert oc.next_unbriefed_week(conn, "BIO2101", "K") is None
    a = mat_k(conn)
    conn.execute("UPDATE occurrences SET week=9, briefing_md='x' WHERE id=?", (a["id"],))
    b = mat_k(conn, "2026-11-02")
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") == 10
    # R is a separate pool
    assert oc.next_unbriefed_week(conn, "MAT2111", "R") == 9

    c = oc.cancel(conn, a["id"], now=at("2026-10-25T20:00"))
    assert (c["status"], c["week"], c["briefing_md"], c["can_cancel"]) == ("cancelled", None, None, False)
    assert c["can_uncancel"] is True and a["can_uncancel"] is False
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") == 9  # week back in the pool
    assert b["week"] is None

    u = oc.uncancel(conn, a["id"], now=at("2026-10-25T20:00"))
    assert u["status"] == "scheduled" and u["week"] is None and u["can_uncancel"] is False


def test_cancel_after_start_conflicts(conn):
    a = mat_k(conn)
    with pytest.raises(oc.Conflict):
        oc.cancel(conn, a["id"], now=at("2026-10-26T10:00"))
    oc.cancel(conn, a["id"], now=at("2026-10-26T09:00"))
    with pytest.raises(oc.Conflict):
        oc.uncancel(conn, a["id"], now=at("2026-10-26T10:30"))
    with pytest.raises(LookupError):
        oc.cancel(conn, 99999, now=at("2026-10-26T09:00"))


def test_cancelled_week_goes_to_pengganti(conn):
    a = mat_k(conn)
    conn.execute("UPDATE occurrences SET week=9 WHERE id=?", (a["id"],))
    oc.cancel(conn, a["id"], now=at("2026-10-25T00:00"))
    p = oc.create_pengganti(conn, "MAT2111", "K", "2026-10-28", "09:00", "10:40", "R1")
    assert p["is_pengganti"] == 1 and p["session_id"] is None and p["status"] == "scheduled"
    assert p["room"] == "R1"
    # pengganti shows on its own date
    assert [o["id"] for o in oc.list_day(conn, "2026-10-28") if o["is_pengganti"]] == [p["id"]]
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") == 9


def test_start_week_respected(conn):
    conn.execute("UPDATE courses SET start_week=12 WHERE code='MAT2111'")
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") == 12
    conn.execute("UPDATE courses SET start_week=99 WHERE code='MAT2111'")
    assert oc.next_unbriefed_week(conn, "MAT2111", "K") is None


@pytest.mark.parametrize("args", [
    ("MAT2111", "X", MON, "09:00", "10:00"),
    ("NOPE", "K", MON, "09:00", "10:00"),
    ("MAT2111", "K", "not-a-date", "09:00", "10:00"),
    ("MAT2111", "K", MON, "9am", "10:00"),
    ("MAT2111", "K", MON, "10:00", "09:00"),
])
def test_pengganti_validation(conn, args):
    with pytest.raises(ValueError):
        oc.create_pengganti(conn, *args)
