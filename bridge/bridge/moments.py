"""Daily Moment: journal day, hidden random ping, state machine, streak, scheduler."""
import asyncio
import logging
import random
import re
import sqlite3
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from . import config, db, media, push

log = logging.getLogger("bridge.moments")

_rng = random.SystemRandom()  # tests replace this
HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DEFAULT_START, DEFAULT_END = "09:00", "21:00"
ON_TIME = timedelta(minutes=30)
MAX_PUSH_ATTEMPTS = 10
EMPTY_ENTRY_AGE = timedelta(hours=6)


def iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def journal_day(t: datetime) -> str:
    """The journal day starts at 05:00 WIB, so 04:59 still belongs to the previous calendar day."""
    return (t.astimezone(config.TZ) - timedelta(hours=5)).date().isoformat()


def _at(day: str, hhmm: str) -> datetime:
    return datetime.combine(date_cls.fromisoformat(day), time.fromisoformat(hhmm), tzinfo=config.TZ)


def closes_at(day: str) -> datetime:
    return _at(day, "05:00") + timedelta(days=1)


def ping_at(row) -> datetime:
    return datetime.fromisoformat(row["ping_at"])


def window_end_dt(row) -> datetime:
    return _at(row["date"], row["window_end"])


# ---- settings ----

def get_settings(conn) -> dict:
    kv = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM app_settings")}
    return {"window_start": kv.get("moment_window_start", DEFAULT_START),
            "window_end": kv.get("moment_window_end", DEFAULT_END)}


def settings_json(conn, now: datetime) -> dict:
    today = journal_day(now)
    exists = conn.execute("SELECT 1 FROM moment_days WHERE date=?", (today,)).fetchone()
    applies = today if not exists else (date_cls.fromisoformat(today) + timedelta(days=1)).isoformat()
    return {**get_settings(conn), "applies_from": applies}


def put_settings(conn, start: str, end: str) -> None:
    if not (HHMM.fullmatch(start) and HHMM.fullmatch(end)):
        raise ValueError("Times must be HH:MM.")
    if start < "05:00":
        raise ValueError("The earliest start is 05:00.")
    if end > "23:59":
        raise ValueError("The latest end is 23:59.")
    mins = lambda s: int(s[:2]) * 60 + int(s[3:])
    if mins(end) - mins(start) < 60:
        raise ValueError("The window must be at least 60 minutes long.")
    for k, v in (("moment_window_start", start), ("moment_window_end", end)):
        conn.execute("INSERT INTO app_settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
    conn.commit()


# ---- the day row ----

def ensure_day(conn, day: str, now: datetime):
    """Create the day's row if missing (picking the hidden ping) and return it. Idempotent."""
    row = conn.execute("SELECT * FROM moment_days WHERE date=?", (day,)).fetchone()
    if row:
        return row
    s = get_settings(conn)
    start_dt, end_dt = _at(day, s["window_start"]), _at(day, s["window_end"])
    lo = max(start_dt, now)
    if lo.microsecond:
        lo = lo.replace(microsecond=0) + timedelta(seconds=1)
    # Created after the window ended (e.g. the Mac woke at 22:00): open at once, no push.
    ping = lo + timedelta(seconds=_rng.randrange(int((end_dt - lo).total_seconds()))) if lo < end_dt else start_dt
    conn.execute("INSERT OR IGNORE INTO moment_days(date, window_start, window_end, ping_at, created_at) VALUES(?,?,?,?,?)",
                 (day, s["window_start"], s["window_end"], iso(ping), iso(now)))
    conn.commit()
    return conn.execute("SELECT * FROM moment_days WHERE date=?", (day,)).fetchone()


def state_of(row, now: datetime) -> str:
    if row["entry_id"] is not None:
        return "submitted_on_time" if row["on_time"] else "submitted_late"
    if now < ping_at(row):
        return "no_ping_yet"
    return "pinged" if row["date"] == journal_day(now) else "missed"


def reference(row) -> datetime:
    return datetime.fromisoformat(row["fired_at"]) if row["push_result"] == "sent" else ping_at(row)


def streak(conn, now: datetime) -> dict:
    today = date_cls.fromisoformat(journal_day(now))
    counted = {date_cls.fromisoformat(r["date"]) for r in conn.execute("SELECT date FROM moment_days WHERE entry_id IS NOT NULL")}
    d = today if today in counted else today - timedelta(days=1)
    current = 0
    while d in counted:
        current += 1
        d -= timedelta(days=1)
    best = run = 0
    prev = None
    for d in sorted(counted):
        run = run + 1 if prev is not None and d == prev + timedelta(days=1) else 1
        best, prev = max(best, run), d
    return {"current": current, "best": best, "today_counted": today in counted,
            "last_counted": max(counted).isoformat() if counted else None}


def moment_state(conn, now: datetime, load_entry) -> dict:
    """MomentState for the current journal day. The real ping time is never sent before it has passed."""
    row = ensure_day(conn, journal_day(now), now)
    state = state_of(row, now)
    hidden = state == "no_ping_yet"
    return {
        "date": row["date"], "state": state,
        "window": {"start": row["window_start"], "end": row["window_end"]},
        "ping_at": None if hidden else row["ping_at"],
        "on_time_until": None if hidden else iso(reference(row) + ON_TIME),
        "closes_at": iso(closes_at(row["date"])),
        "submitted_at": row["submitted_at"], "on_time": None if row["on_time"] is None else bool(row["on_time"]),
        "push_result": None if hidden else row["push_result"],
        "entry": load_entry(conn, row["entry_id"]) if row["entry_id"] is not None else None,
        "streak": streak(conn, now),
    }


def history(conn, days: int, now: datetime) -> list[dict]:
    first = conn.execute("SELECT MIN(date) AS d FROM moment_days").fetchone()["d"]
    if first is None:
        return []
    today = date_cls.fromisoformat(journal_day(now))
    d = max(today - timedelta(days=days - 1), date_cls.fromisoformat(first))
    rows = {r["date"]: r for r in conn.execute("SELECT * FROM moment_days WHERE date>=?", (d.isoformat(),))}
    out = []
    while d <= today:
        r = rows.get(d.isoformat())
        state = "missed" if r is None and d < today else "no_ping_yet" if r is None else state_of(r, now)
        thumb = None
        if r is not None and r["entry_id"] is not None:
            m = conn.execute("SELECT thumb FROM journal_media WHERE entry_id=? ORDER BY position LIMIT 1", (r["entry_id"],)).fetchone()
            thumb = f"/journal-media/{m['thumb']}" if m and m["thumb"] else None
        out.append({"date": d.isoformat(), "state": state,
                    "on_time": None if r is None or r["on_time"] is None else bool(r["on_time"]),
                    "entry_id": r["entry_id"] if r else None, "thumb_url": thumb})
        d += timedelta(days=1)
    return out


def submit(conn, day: str, now: datetime, item, photo, caption) -> list:
    """Commit a Moment (new or replacement) in one transaction; return the old files to unlink after commit."""
    ts = iso(now)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM moment_days WHERE date=?", (day,)).fetchone()
        old, carry, old_files = row["entry_id"], "", []
        if old is not None:
            carry = conn.execute("SELECT text FROM journal_entries WHERE id=?", (old,)).fetchone()["text"]
            for m in conn.execute("SELECT file, thumb, poster, src_path FROM journal_media WHERE entry_id=?", (old,)):
                old_files += [m["file"] and config.JOURNAL_DIR / m["file"], m["thumb"] and config.JOURNAL_DIR / m["thumb"],
                              m["poster"] and config.JOURNAL_DIR / m["poster"], m["src_path"]]
            # The old entry must go first: the unique moment_date index is checked per statement.
            conn.execute("DELETE FROM journal_entries WHERE id=?", (old,))
        cur = conn.execute(
            "INSERT INTO journal_entries(kind, day, moment_date, text, created_at, updated_at) VALUES('moment',?,?,?,?,?)",
            (day, day, caption or carry, ts, ts))
        media.insert_media(conn, cur.lastrowid, 0, item, photo)
        on_time = 1 if now <= reference(row) + ON_TIME else 0
        conn.execute("UPDATE moment_days SET entry_id=?, submitted_at=COALESCE(submitted_at,?), on_time=COALESCE(on_time,?) WHERE date=?",
                     (cur.lastrowid, ts, on_time, day))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return old_files


# ---- scheduler ----

def purge_empty_entries(conn, now: datetime) -> None:
    conn.execute("DELETE FROM journal_entries WHERE kind='free' AND text='' AND created_at<? "
                 "AND NOT EXISTS (SELECT 1 FROM journal_media WHERE entry_id=journal_entries.id)", (iso(now - EMPTY_ENTRY_AGE),))
    conn.commit()


def _mark(conn, day: str, now: datetime, result: str) -> None:
    conn.execute("UPDATE moment_days SET fired_at=?, push_result=? WHERE date=? AND fired_at IS NULL", (iso(now), result, day))
    conn.commit()


def tick(now: datetime | None = None) -> None:
    now = now or config.now()
    day = journal_day(now)
    c = db.connect()
    try:
        row = ensure_day(c, day, now)
        _fire(c, row, day, now)
        try:  # after the ping, so a lock error in this housekeeping write can never skip it
            purge_empty_entries(c, now)
        except sqlite3.Error:
            log.warning("purge of empty entries failed", exc_info=True)
    finally:
        c.close()


def _fire(c, row, day: str, now: datetime) -> None:
    if row["fired_at"] is not None or now < ping_at(row):
        return
    if row["entry_id"] is not None:
        return _mark(c, day, now, "skipped_submitted")
    if now >= window_end_dt(row):
        return _mark(c, day, now, "skipped_late")
    if c.execute("SELECT 1 FROM push_subscriptions LIMIT 1").fetchone() is None:
        return _mark(c, day, now, "no_subscribers")
    # Claim before sending: a crash after this loses the ping but can never send it twice.
    # entry_id IS NULL: a Moment posted since the row was read must not get a ping.
    claimed = c.execute("UPDATE moment_days SET fired_at=?, push_result='failed', push_attempts=push_attempts+1 "
                        "WHERE date=? AND fired_at IS NULL AND entry_id IS NULL", (iso(now), day))
    c.commit()
    if claimed.rowcount == 0:
        return
    res = push.send_all(c, push.moment_payload(day))  # no transaction held during HTTP
    if res.sent > 0:
        c.execute("UPDATE moment_days SET push_result='sent' WHERE date=?", (day,))
    elif res.transient > 0 and now < window_end_dt(row) and row["push_attempts"] + 1 < MAX_PUSH_ATTEMPTS:
        c.execute("UPDATE moment_days SET fired_at=NULL, push_result=NULL WHERE date=?", (day,))  # retry next tick
    c.commit()


async def scheduler_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("moment scheduler tick failed")
        await asyncio.sleep(30)
