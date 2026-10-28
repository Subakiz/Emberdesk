"""Personal-use adapter for screentime-archive; Apple's source files stay read-only."""
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import threading
import time as clock
from contextlib import closing
from datetime import datetime, time, timedelta

from . import ai, config, db

APPS = {"com.zhiliaoapp.musically": "tiktok",
        "com.burbn.instagram": "instagram", "com.instagram.Instagram": "instagram"}
_sync_lock = threading.Lock()


def state(conn):
    row = conn.execute("SELECT * FROM screen_time_sync WHERE id=1").fetchone()
    return {"installed": config.screen_time_binary().is_file(),
            "last_synced": row["last_synced"] if row else None,
            "error": row["error"] if row else None}


def import_archive(conn):
    path = config.screen_time_archive()
    end = config.now()
    start = datetime.combine(end.date() - timedelta(days=29), time(), config.TZ)
    buckets = {}
    # shortcut: native bundle IDs only; add browser-domain tracking if website usage is needed.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as source:
        placeholders = ",".join("?" for _ in APPS)
        rows = source.execute(
            f"SELECT bundle_id, start_ts, end_ts FROM sessions WHERE bundle_id IN ({placeholders}) "
            "AND end_ts>? AND start_ts<?", (*APPS, start.timestamp(), end.timestamp()))
        for bundle, first, last in rows:
            if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (first, last)):
                continue
            first, last = max(first, start.timestamp()), min(last, end.timestamp())
            while first < last:
                day = datetime.fromtimestamp(first, config.TZ).date()
                midnight = datetime.combine(day + timedelta(days=1), time(), config.TZ).timestamp()
                stop = min(last, midnight)
                buckets.setdefault((day.isoformat(), APPS[bundle]), []).append((first, stop))
                first = stop
    totals = {}
    for (day, app), intervals in buckets.items():
        seconds, stop = 0, 0
        # Union sessions: re-imports and overlapping device records must not inflate usage.
        for first, last in sorted(intervals):
            seconds += max(0, last - max(first, stop))
            stop = max(stop, last)
        totals.setdefault(day, {"tiktok": 0, "instagram": 0})[app] = round(seconds)
    stamp = config.now().isoformat()
    with conn:
        conn.execute("DELETE FROM screen_time_days WHERE source='archive' AND date>=? AND date<=?",
                     (start.date().isoformat(), end.date().isoformat()))
        for day, values in totals.items():
            conn.execute("INSERT INTO screen_time_days VALUES(?,?,?,?,?)",
                         (day, "archive", values["tiktok"], values["instagram"], stamp))
        conn.execute("INSERT INTO screen_time_sync VALUES(1,?,NULL) ON CONFLICT(id) DO UPDATE "
                     "SET last_synced=excluded.last_synced,error=NULL", (stamp,))
    return len(totals)


def refresh(conn):
    if not _sync_lock.acquire(blocking=False):
        return state(conn)
    try:
        binary = config.screen_time_binary()
        if not binary.is_file():
            raise RuntimeError("Reader not installed. Follow Screen Time setup below, then sync again.")
        # launchd gives the reader its own TCC identity instead of inheriting the bridge's permissions.
        target = f"gui/{os.getuid()}/com.boredommaxxing.screentime"
        result = subprocess.run(["/bin/launchctl", "kickstart", target],
                                capture_output=True, timeout=10, text=True, stdin=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError("Reader background job is unavailable. Run the Screen Time setup script, then sync again.")
        deadline = clock.monotonic() + 90
        while True:
            result = subprocess.run(["/bin/launchctl", "print", target], capture_output=True, timeout=10, text=True)
            if result.returncode or re.search(r"^\s*state = not running$", result.stdout, re.MULTILINE):
                break
            if clock.monotonic() >= deadline:
                raise RuntimeError("Screen Time import is still running. Try syncing again in a minute.")
            clock.sleep(0.2)
        if result.returncode or not re.search(r"last exit code\s*=\s*0\b", result.stdout):
            raise RuntimeError("Screen Time could not be read. Grant Full Disk Access to the reader, "
                               "enable Share Across Devices, then sync again. Saved totals are unchanged.")
        import_archive(conn)
    except (OSError, sqlite3.Error, subprocess.TimeoutExpired, RuntimeError) as error:
        with conn:
            conn.execute("INSERT INTO screen_time_sync(id,error) VALUES(1,?) "
                         "ON CONFLICT(id) DO UPDATE SET error=excluded.error", (str(error),))
    finally:
        _sync_lock.release()
    return state(conn)


def scheduled_refresh():
    if config.screen_time_binary().is_file():
        with closing(db.connect()) as conn:
            refresh(conn)


def summary(conn, days):
    end = config.today()
    start = (datetime.fromisoformat(end).date() - timedelta(days=days - 1)).isoformat()
    rows = conn.execute("SELECT * FROM screen_time_days WHERE date BETWEEN ? AND ? ORDER BY date DESC, source DESC",
                        (start, end)).fetchall()
    chosen = {}
    for row in rows:
        # A manual daily total replaces the imported pair, never adds to it.
        if row["date"] not in chosen:
            chosen[row["date"]] = dict(row)
    history = list(chosen.values())
    totals = {app: sum(r[f"{app}_seconds"] for r in history) for app in ("tiktok", "instagram")}
    budget = [(r["date"], r["tiktok_seconds"], r["instagram_seconds"]) for r in history]
    fingerprint = hashlib.sha256(json.dumps([start, end, budget]).encode()).hexdigest()
    cached = conn.execute("SELECT ideas_json FROM screen_time_ideas WHERE fingerprint=?", (fingerprint,)).fetchone()
    return {"start": start, "end": end, "days": days, "history": history,
            "tiktok_seconds": totals["tiktok"], "instagram_seconds": totals["instagram"],
            "total_seconds": sum(totals.values()), "fingerprint": fingerprint,
            "ideas": json.loads(cached[0]) if cached else [], "sync": state(conn)}


def generate(conn, days):
    data = summary(conn, days)
    minutes = data["total_seconds"] // 60
    if minutes < 1:
        raise ValueError("Log at least one minute before generating ideas.")
    if not data["ideas"]:
        courses = [r[0] for r in conn.execute("SELECT name FROM courses ORDER BY code")]
        ideas = ai.time_equivalents(minutes, courses)
        with conn:
            conn.execute("INSERT OR IGNORE INTO screen_time_ideas VALUES(?,?,?)",
                         (data["fingerprint"], json.dumps(ideas), config.now().isoformat()))
        data["ideas"] = ideas
    return data
