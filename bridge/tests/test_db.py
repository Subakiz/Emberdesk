import pytest

from bridge import db

TABLES = {"courses", "sessions", "occurrences", "tasks", "lock_log", "notes",
          "ypt_days", "summaries", "quotes", "job_runs"}


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_schema_created(conn):
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLES <= names
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(courses)")]
    assert "start_week" in cols


def test_seed_idempotent(conn):
    assert (count(conn, "courses"), count(conn, "sessions")) == (8, 12)
    q = count(conn, "quotes")
    db.init_db(conn)
    db.init_db(conn)
    assert (count(conn, "courses"), count(conn, "sessions"), count(conn, "quotes")) == (8, 12, q)


def test_foreign_keys_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(Exception):
        conn.execute("INSERT INTO sessions(course_code, kind) VALUES('NOPE','K')")
