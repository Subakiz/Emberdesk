from datetime import datetime
from pathlib import Path

import pytest

from bridge import ai, jobs, materials, ypt


def first_scheduled_date(conn):
    """A date whose weekday has a seeded session (also a date in the future of nothing; deterministic)."""
    wd = conn.execute("SELECT weekday, course_code, kind FROM sessions ORDER BY id LIMIT 1").fetchone()
    d = datetime(2026, 10, 19 + wd["weekday"])  # 2026-10-19 is a Monday
    return d.strftime("%Y-%m-%d"), wd["course_code"], wd["kind"]


@pytest.fixture
def fakes(monkeypatch, conn):
    date, code, kind = first_scheduled_date(conn)
    monkeypatch.setattr(materials, "for_course", lambda c, course: {(kind, 1): Path("/x/s.pdf")} if course == code else {})
    calls = {"brief": 0, "tasks": 0, "sum": 0}

    def brief(*a):
        calls["brief"] += 1
        return "## Key ideas"

    def tasks(d, ctx):
        calls["tasks"] += 1
        return [{"kind": "class", "text": "a"}, {"kind": "physical", "text": "b"}, {"kind": "wildcard", "text": "c"}]

    monkeypatch.setattr(ai, "brief", brief)
    monkeypatch.setattr(ai, "make_tasks", tasks)
    monkeypatch.setattr(ai, "summarize_day", lambda *a: calls.__setitem__("sum", calls["sum"] + 1) or "sum")
    monkeypatch.setattr(jobs.config, "settings", lambda: type("S", (), {"YPT_URL": None})())
    return date, code, kind, calls


def test_brief_occurrence(conn, fakes):
    from bridge import occurrences

    date, code, kind, calls = fakes
    occ = next(o for o in occurrences.list_day(conn, date) if o["course_code"] == code and o["kind"] == kind)
    assert jobs.brief_occurrence(conn, occ["id"]) is True
    o = occurrences.get(conn, occ["id"])
    assert o["week"] == 1 and o["briefing_md"] == "## Key ideas"
    assert conn.execute("SELECT briefed_at FROM occurrences WHERE id=?", (occ["id"],)).fetchone()[0]
    assert jobs.brief_occurrence(conn, occ["id"]) is False  # already briefed
    assert calls["brief"] == 1


def test_brief_skips_cancelled_and_no_material(conn, fakes):
    from bridge import occurrences

    date, code, kind, calls = fakes
    other = next(o for o in occurrences.list_day(conn, date) if o["course_code"] != code)
    assert jobs.brief_occurrence(conn, other["id"]) is False  # no material
    assert jobs.brief_occurrence(conn, 99999) is False
    conn.execute("UPDATE occurrences SET status='cancelled' WHERE course_code=?", (code,))
    conn.commit()
    occ = next(o for o in occurrences.list_day(conn, date) if o["course_code"] == code)
    assert jobs.brief_occurrence(conn, occ["id"]) is False
    assert calls["brief"] == 0


def test_ensure_tasks_idempotent(conn, fakes):
    date, *_, calls = fakes
    assert jobs.ensure_tasks(conn, date) is True
    assert jobs.ensure_tasks(conn, date) is False
    rows = conn.execute("SELECT slot, kind FROM tasks WHERE date=? ORDER BY slot", (date,)).fetchall()
    assert [(r["slot"], r["kind"]) for r in rows] == [(1, "class"), (2, "physical"), (3, "wildcard")]
    assert calls["tasks"] == 1


def test_fetch_ypt_skip_and_store(conn, monkeypatch):
    monkeypatch.setattr(jobs.config, "settings", lambda: type("S", (), {"YPT_URL": None})())
    assert jobs.fetch_ypt(conn) is False
    monkeypatch.setattr(jobs.config, "settings", lambda: type("S", (), {"YPT_URL": "http://x"})())
    monkeypatch.setattr(ypt, "fetch_me_all", lambda: {"study_minutes": 30})
    monkeypatch.setattr(jobs.config, "today", lambda: "2026-10-21")
    assert jobs.fetch_ypt(conn) is True
    r = conn.execute("SELECT date, total_minutes FROM ypt_days").fetchone()
    assert (r["date"], r["total_minutes"]) == ("2026-10-20", 30)


def test_summarize(conn, fakes):
    date, *_, calls = fakes
    assert jobs.summarize(conn, date) is False  # nothing to summarize
    conn.execute("INSERT INTO notes(date, text) VALUES(?, 'learned x')", (date,))
    assert jobs.summarize(conn, date) is True
    assert jobs.summarize(conn, date) is False
    assert calls["sum"] == 1


def test_evening_isolates_failures_and_is_idempotent(conn, fakes, monkeypatch):
    date, code, kind, calls = fakes
    monkeypatch.setattr(jobs.config, "today", lambda: "2026-10-20")
    monkeypatch.setattr(ai, "make_tasks", lambda *a: (_ for _ in ()).throw(ai.AIError("boom")))
    res = jobs.evening(date)
    assert res["tasks"].startswith("error")
    assert any(v is True for k, v in res.items() if k.startswith("brief:"))
    assert res["ypt"] is False
    run = conn.execute("SELECT * FROM job_runs").fetchone()
    assert run["ok"] == 0 and "tasks: ERROR" in run["log"] and run["finished_at"]
    # rerun with working AI: no duplicate briefing work, tasks now created
    monkeypatch.setattr(ai, "make_tasks", lambda *a: [{"kind": "class", "text": "a"}] * 3)
    briefs = calls["brief"]
    res = jobs.evening(date)
    assert calls["brief"] == briefs and res["tasks"] is True
    assert conn.execute("SELECT COUNT(*) FROM job_runs WHERE ok=1").fetchone()[0] == 1
