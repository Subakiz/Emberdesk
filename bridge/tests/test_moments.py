import sqlite3
from datetime import timedelta

import pytest

from bridge import config, db, moments, push
from journal_helpers import SUB_APPLE, wib


def add_sub(conn, endpoint=SUB_APPLE):
    conn.execute("INSERT INTO push_subscriptions(endpoint, p256dh, auth, created_at) VALUES(?,?,?,?)", (endpoint, "k", "a", "x"))
    conn.commit()


def day_row(conn, d="2026-10-26"):
    return conn.execute("SELECT * FROM moment_days WHERE date=?", (d,)).fetchone()


def count_day(conn, d, now=None):
    """Insert a day that counts for the streak, without going through uploads."""
    now = now or wib(int(d[-2:]), 12)
    ts = moments.iso(now)
    conn.execute("INSERT OR IGNORE INTO moment_days(date, window_start, window_end, ping_at, created_at) VALUES(?,?,?,?,?)",
                 (d, "09:00", "21:00", ts, ts))
    cur = conn.execute("INSERT INTO journal_entries(kind, day, moment_date, text, created_at, updated_at) VALUES('moment',?,?,'',?,?)",
                       (d, d, ts, ts))
    conn.execute("UPDATE moment_days SET entry_id=?, submitted_at=?, on_time=1 WHERE date=?", (cur.lastrowid, ts, d))
    conn.commit()
    return cur.lastrowid


# ---- schema ----

def test_init_db_idempotent(conn):
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM journal_entries").fetchone()[0] == 0


def test_schema_constraints(conn):
    ins = "INSERT INTO journal_entries(kind, day, moment_date, text, created_at, updated_at) VALUES(?,?,?,'','t','t')"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, ("bogus", "2026-10-26", None))
    conn.execute(ins, ("moment", "2026-10-26", "2026-10-26"))
    with pytest.raises(sqlite3.IntegrityError):  # second moment entry for the same date
        conn.execute(ins, ("moment", "2026-10-26", "2026-10-26"))
    with pytest.raises(sqlite3.IntegrityError):  # moment without moment_date
        conn.execute(ins, ("moment", "2026-10-27", None))
    with pytest.raises(sqlite3.IntegrityError):  # free with moment_date
        conn.execute(ins, ("free", "2026-10-27", "2026-10-27"))


# ---- journal day ----

def test_journal_day_boundary():
    assert moments.journal_day(wib(27, 4, 59, 59)) == "2026-10-26"
    assert moments.journal_day(wib(27, 5, 0, 0)) == "2026-10-27"


# ---- settings ----

def test_settings_defaults_and_validation(client, clock):
    assert client.get("/api/journal/settings").json() == {"window_start": "09:00", "window_end": "21:00", "applies_from": "2026-10-26"}
    ok = client.put("/api/journal/settings", json={"window_start": "10:00", "window_end": "20:00"})
    assert ok.status_code == 200 and ok.json()["window_start"] == "10:00"
    for s, e in [("9:00", "21:00"), ("04:59", "21:00"), ("09:00", "24:00"), ("09:00", "09:59"), ("22:30", "23:00"), ("09:00", "nope")]:
        r = client.put("/api/journal/settings", json={"window_start": s, "window_end": e})
        assert r.status_code == 422, (s, e)
    assert client.put("/api/journal/settings", json={"window_start": "05:00", "window_end": "23:59"}).status_code == 200


def test_settings_applies_from_and_snapshot(client, clock, rng):
    client.get("/api/journal/moment")  # creates today's row with the 09:00-21:00 snapshot
    r = client.put("/api/journal/settings", json={"window_start": "12:00", "window_end": "13:00"}).json()
    assert r["applies_from"] == "2026-10-27"
    assert client.get("/api/journal/moment").json()["window"] == {"start": "09:00", "end": "21:00"}
    clock.t = wib(27, 6, 0)
    assert client.get("/api/journal/moment").json()["window"] == {"start": "12:00", "end": "13:00"}


# ---- ensure_day ----

def test_ensure_day_ping_inside_window_and_idempotent(conn, rng):
    rng.v = 5000
    now = wib(26, 5, 1)
    r = moments.ensure_day(conn, "2026-10-26", now)
    assert r["ping_at"] == moments.iso(wib(26, 9, 0) + timedelta(seconds=5000))
    rng.v = 1
    assert moments.ensure_day(conn, "2026-10-26", wib(26, 7)) ["ping_at"] == r["ping_at"]


def test_ensure_day_created_midday_picks_after_now(conn, rng):
    rng.v = 0
    r = moments.ensure_day(conn, "2026-10-26", wib(26, 13, 0, 0))
    assert r["ping_at"] == moments.iso(wib(26, 13, 0))
    rng.v = 10**9
    r = moments.ensure_day(conn, "2026-10-27", wib(27, 13, 30, 0, ))
    assert moments.ping_at(r) < wib(27, 21, 0)


def test_ensure_day_after_window_opens_at_start(conn, rng):
    r = moments.ensure_day(conn, "2026-10-26", wib(26, 22, 0))
    assert r["ping_at"] == moments.iso(wib(26, 9, 0))


# ---- state ----

def test_state_machine_and_hidden_ping(client, clock, rng):
    rng.v = 3600  # ping at 10:00:00
    clock.t = wib(26, 5, 30)
    r = client.get("/api/journal/moment")
    s = r.json()
    assert s["state"] == "no_ping_yet" and s["ping_at"] is None and s["on_time_until"] is None and s["push_result"] is None
    assert "10:00:00" not in r.text and moments.iso(wib(26, 10, 0)) not in r.text
    assert s["closes_at"] == "2026-10-27T05:00:00+07:00" and s["window"] == {"start": "09:00", "end": "21:00"}
    assert client.get("/api/journal/moments").json()["days"][-1]["state"] == "no_ping_yet"
    assert "10:00:00" not in client.get("/api/journal/moments").text
    clock.t = wib(26, 10, 0)
    s = client.get("/api/journal/moment").json()
    assert s["state"] == "pinged" and s["ping_at"] == "2026-10-26T10:00:00+07:00" and s["on_time_until"] == "2026-10-26T10:30:00+07:00"
    assert s["entry"] is None and s["streak"]["current"] == 0


# ---- streak ----

def test_streak_edge_cases(conn):
    now = wib(26, 12)
    assert moments.streak(conn, now) == {"current": 0, "best": 0, "today_counted": False, "last_counted": None}
    for d in ("2026-10-23", "2026-10-24", "2026-10-25"):
        count_day(conn, d)
    s = moments.streak(conn, now)  # today not posted yet: run ending yesterday
    assert s == {"current": 3, "best": 3, "today_counted": False, "last_counted": "2026-10-25"}
    count_day(conn, "2026-10-26")
    assert moments.streak(conn, now)["current"] == 4
    assert moments.streak(conn, wib(27, 4, 59))["current"] == 4  # still journal day 26
    s = moments.streak(conn, wib(27, 5, 0))  # day 27 not posted: run ending 26 still counts
    assert s["current"] == 4 and not s["today_counted"]
    assert moments.streak(conn, wib(28, 5, 0))["current"] == 0  # 27 missed
    assert moments.streak(conn, wib(28, 5, 0))["best"] == 4


def test_streak_gap_and_best(conn):
    for d in ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-25", "2026-10-26"):
        count_day(conn, d)
    s = moments.streak(conn, wib(26, 12))
    assert (s["current"], s["best"]) == (2, 4) and s["best"] >= s["current"]
    conn.execute("DELETE FROM moment_days WHERE date='2026-10-25'")  # yesterday missed, today posted
    conn.commit()
    assert moments.streak(conn, wib(26, 12))["current"] == 1


def test_streak_moment_at_0430_counts_for_previous_day(client, clock, rng, tools):
    from journal_helpers import JPEG

    clock.t = wib(26, 10)
    client.get("/api/journal/moment")
    clock.t = wib(27, 4, 30)
    r = client.post("/api/journal/moment", files={"file": ("a.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201 and r.json()["date"] == "2026-10-26"
    assert r.json()["streak"]["last_counted"] == "2026-10-26"


# ---- history ----

def test_history_missed_and_start(conn, rng):
    assert moments.history(conn, 30, wib(26, 12)) == []
    count_day(conn, "2026-10-22")
    moments.ensure_day(conn, "2026-10-26", wib(26, 6))
    h = moments.history(conn, 30, wib(26, 12))
    assert [d["date"] for d in h] == [f"2026-10-{n}" for n in range(22, 27)]
    assert [d["state"] for d in h] == ["submitted_on_time", "missed", "missed", "missed", "pinged" if moments.ping_at(day_row(conn)) <= wib(26, 12) else "no_ping_yet"]
    assert [d["date"] for d in moments.history(conn, 2, wib(26, 12))] == ["2026-10-25", "2026-10-26"]


# ---- tick ----

def fire(conn, d="2026-10-26", ping=wib(26, 10, 0)):
    conn.execute("UPDATE moment_days SET ping_at=? WHERE date=?", (moments.iso(ping), d))
    conn.commit()


@pytest.fixture
def fake_send(monkeypatch):
    calls = []
    plan = {"status": 201}

    def send(sub, data):
        calls.append((sub, data))
        s = plan["status"]
        if isinstance(s, Exception):
            raise s
        return s

    monkeypatch.setattr(push, "_send", send)
    return type("S", (), {"calls": calls, "plan": plan})


def test_tick_fires_once(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn)
    moments.tick(wib(26, 10, 0, 5))
    moments.tick(wib(26, 10, 0, 35))
    assert len(fake_send.calls) == 1
    r = day_row(conn)
    assert r["push_result"] == "sent" and r["fired_at"] == "2026-10-26T10:00:05+07:00" and r["push_attempts"] == 1
    assert '"tag": "moment-2026-10-26"' in fake_send.calls[0][1]


def test_tick_before_ping_does_nothing(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 15))
    moments.tick(wib(26, 14))
    assert not fake_send.calls and day_row(conn)["fired_at"] is None


def test_tick_catch_up_after_sleep(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    moments.tick(wib(26, 13, 0))
    assert len(fake_send.calls) == 1 and day_row(conn)["push_result"] == "sent"


def test_tick_after_window_end_skipped_late(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    moments.tick(wib(26, 21, 0))
    assert not fake_send.calls and day_row(conn)["push_result"] == "skipped_late"


def test_tick_skipped_submitted(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    count_day(conn, "2026-10-26")
    moments.tick(wib(26, 11))
    assert not fake_send.calls and day_row(conn)["push_result"] == "skipped_submitted"


def test_tick_no_subscribers(conn, rng, fake_send):
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    moments.tick(wib(26, 11))
    assert not fake_send.calls and day_row(conn)["push_result"] == "no_subscribers"


def test_tick_transient_releases_then_retries(conn, rng, fake_send):
    import requests

    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    fake_send.plan["status"] = requests.ConnectionError("down")
    moments.tick(wib(26, 10, 1))
    r = day_row(conn)
    assert r["fired_at"] is None and r["push_result"] is None and r["push_attempts"] == 1
    fake_send.plan["status"] = 503
    moments.tick(wib(26, 10, 2))
    assert day_row(conn)["fired_at"] is None and day_row(conn)["push_attempts"] == 2
    fake_send.plan["status"] = 201
    moments.tick(wib(26, 10, 3))
    r = day_row(conn)
    assert r["push_result"] == "sent" and r["push_attempts"] == 3 and len(fake_send.calls) == 3
    assert conn.execute("SELECT fail_count FROM push_subscriptions").fetchone()[0] == 0


def test_tick_transient_stops_after_window_or_ten_attempts(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    conn.execute("UPDATE moment_days SET push_attempts=9")
    conn.commit()
    fake_send.plan["status"] = 500
    moments.tick(wib(26, 10, 1))
    assert day_row(conn)["push_result"] == "failed"
    moments.tick(wib(26, 10, 2))
    assert len(fake_send.calls) == 1


def test_tick_permanent_403_no_retry(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    fake_send.plan["status"] = 403
    moments.tick(wib(26, 10, 1))
    moments.tick(wib(26, 10, 2))
    assert len(fake_send.calls) == 1 and day_row(conn)["push_result"] == "failed"
    assert conn.execute("SELECT fail_count FROM push_subscriptions").fetchone()[0] == 1


def test_tick_410_deletes_subscription(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    fake_send.plan["status"] = 410
    moments.tick(wib(26, 10, 1))
    assert conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0
    assert day_row(conn)["push_result"] == "failed"


def test_tick_preset_fired_at_means_no_send(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    conn.execute("UPDATE moment_days SET fired_at='2026-10-26T10:00:00+07:00', push_result='sent'")
    conn.commit()
    moments.tick(wib(26, 11))
    assert not fake_send.calls


def test_tick_purges_old_empty_entries(conn, rng):
    old, new, full = (wib(26, 1, 0), wib(26, 9, 0), wib(26, 1, 0))
    ins = "INSERT INTO journal_entries(kind, day, text, created_at, updated_at) VALUES('free','2026-10-25',?,?,?)"
    a = conn.execute(ins, ("", moments.iso(old), moments.iso(old))).lastrowid
    b = conn.execute(ins, ("", moments.iso(new), moments.iso(new))).lastrowid
    c = conn.execute(ins, ("text", moments.iso(full), moments.iso(full))).lastrowid
    conn.commit()
    moments.tick(wib(26, 10))
    ids = {r[0] for r in conn.execute("SELECT id FROM journal_entries")}
    assert ids == {b, c} and a not in ids


def test_claim_skips_when_moment_was_posted_after_the_row_was_read(conn, rng, fake_send):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))
    stale = day_row(conn)  # what tick() saw
    count_day(conn, "2026-10-26")  # the Moment lands before the claim UPDATE
    moments._fire(conn, stale, "2026-10-26", wib(26, 10, 1))
    assert not fake_send.calls and day_row(conn)["fired_at"] is None
    moments.tick(wib(26, 10, 2))
    assert not fake_send.calls and day_row(conn)["push_result"] == "skipped_submitted"


def test_purge_lock_error_does_not_skip_the_ping(conn, rng, fake_send, monkeypatch):
    add_sub(conn)
    moments.ensure_day(conn, "2026-10-26", wib(26, 5))
    fire(conn, ping=wib(26, 10))

    def locked(*a):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(moments, "purge_empty_entries", locked)
    moments.tick(wib(26, 10, 1))
    assert len(fake_send.calls) == 1 and day_row(conn)["push_result"] == "sent"
