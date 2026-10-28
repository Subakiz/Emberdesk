import json
import sqlite3

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT, start_week INTEGER);
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, course_code TEXT REFERENCES courses(code),
  kind TEXT CHECK(kind IN('K','R')), class_no INTEGER, weekday INTEGER,
  start TEXT, end TEXT, room TEXT);
CREATE TABLE IF NOT EXISTS occurrences(
  id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id),
  course_code TEXT NOT NULL REFERENCES courses(code), kind TEXT NOT NULL, date TEXT NOT NULL,
  start TEXT NOT NULL, end TEXT NOT NULL, room TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN('scheduled','cancelled')),
  is_pengganti INTEGER NOT NULL DEFAULT 0,
  week INTEGER, briefing_md TEXT, briefed_at TEXT,
  UNIQUE(session_id, date));
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, slot INTEGER CHECK(slot IN(1,2,3)),
  kind TEXT CHECK(kind IN('class','physical','wildcard')), text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','passed','failed')),
  photo_path TEXT, verdict_reason TEXT, appealed INTEGER NOT NULL DEFAULT 0, appeal_note TEXT,
  UNIQUE(date, slot));
CREATE TABLE IF NOT EXISTS lock_log(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, app TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes(date TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ypt_days(
  date TEXT PRIMARY KEY, total_minutes INTEGER, subjects_json TEXT, raw_json TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS summaries(date TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS quotes(
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, author TEXT NOT NULL, source TEXT,
  custom INTEGER NOT NULL DEFAULT 0, UNIQUE(text, author));
CREATE TABLE IF NOT EXISTS job_runs(
  id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, ok INTEGER, log TEXT);

CREATE TABLE IF NOT EXISTS journal_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reuse ids: the composer and the SW cache hold them
  kind TEXT NOT NULL CHECK(kind IN('free','moment')),
  day TEXT NOT NULL,                         -- journal day of created_at (05:00 boundary), YYYY-MM-DD
  moment_date TEXT,                          -- = day when kind='moment', NULL when kind='free'
  text TEXT NOT NULL DEFAULT '' CHECK(length(text) <= 20000),
  created_at TEXT NOT NULL,                  -- ISO +07:00
  updated_at TEXT NOT NULL,
  CHECK((kind = 'moment') = (moment_date IS NOT NULL)));
CREATE UNIQUE INDEX IF NOT EXISTS journal_entries_moment ON journal_entries(moment_date) WHERE moment_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS journal_entries_day ON journal_entries(day, id);

CREATE TABLE IF NOT EXISTS journal_media(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
  position INTEGER NOT NULL DEFAULT 0,       -- upload order inside the entry, 0-based
  kind TEXT NOT NULL CHECK(kind IN('photo','video','audio')),
  status TEXT NOT NULL CHECK(status IN('processing','ready','error')),
  error TEXT,                                -- short human-readable reason when status='error'
  uid TEXT NOT NULL UNIQUE CHECK(length(uid) = 32),  -- uuid4().hex, base of every file name for this item
  file TEXT,                                 -- basename in JOURNAL_DIR, NULL until ready
  thumb TEXT,                                -- basename, photo and video only, may stay NULL for video
  poster TEXT,                               -- basename, video only, may stay NULL
  src_path TEXT,                             -- absolute path of the raw upload in JOURNAL_TMP while status='processing'
  width INTEGER, height INTEGER,             -- video only (post-rotation, from ffprobe of the encoded file)
  duration_ms INTEGER,                       -- video and audio only
  bytes INTEGER,                             -- size of the served file
  transcript_status TEXT CHECK(transcript_status IN('pending','done','error','none')),
  transcript_text TEXT,
  transcript_lang TEXT,                      -- whisper's detected language code, e.g. 'id' or 'en'
  transcript_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK((kind = 'photo') = (transcript_status IS NULL)));
CREATE INDEX IF NOT EXISTS journal_media_entry ON journal_media(entry_id, position);
CREATE INDEX IF NOT EXISTS journal_media_work ON journal_media(status, transcript_status, id);

CREATE TABLE IF NOT EXISTS moment_days(
  date TEXT PRIMARY KEY,                     -- journal day D
  window_start TEXT NOT NULL,                -- snapshot of the setting when the row was created
  window_end TEXT NOT NULL,
  ping_at TEXT NOT NULL,                     -- ISO +07:00, secret until it has passed
  fired_at TEXT,                             -- when the scheduler claimed the ping (see 5.3)
  push_result TEXT CHECK(push_result IN('sent','no_subscribers','failed','skipped_submitted','skipped_late')),
  push_attempts INTEGER NOT NULL DEFAULT 0,
  entry_id INTEGER REFERENCES journal_entries(id) ON DELETE SET NULL,
  submitted_at TEXT,                         -- first submission time for D, kept across replacements
  on_time INTEGER CHECK(on_time IN(0,1)),
  created_at TEXT NOT NULL,
  CHECK((fired_at IS NULL) = (push_result IS NULL)),
  CHECK(entry_id IS NULL OR (submitted_at IS NOT NULL AND on_time IS NOT NULL)));
CREATE INDEX IF NOT EXISTS moment_days_entry ON moment_days(entry_id);

CREATE TABLE IF NOT EXISTS push_subscriptions(
  id INTEGER PRIMARY KEY,
  endpoint TEXT NOT NULL UNIQUE CHECK(endpoint LIKE 'https://%' AND length(endpoint) <= 1024),
  p256dh TEXT NOT NULL CHECK(length(p256dh) <= 200),
  auth TEXT NOT NULL CHECK(length(auth) <= 200),
  user_agent TEXT,
  created_at TEXT NOT NULL,
  last_ok_at TEXT,
  fail_count INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS screen_time_days(
  date TEXT NOT NULL, source TEXT NOT NULL CHECK(source IN('archive','manual')),
  tiktok_seconds INTEGER NOT NULL CHECK(tiktok_seconds BETWEEN 0 AND 86400),
  instagram_seconds INTEGER NOT NULL CHECK(instagram_seconds BETWEEN 0 AND 86400),
  updated_at TEXT NOT NULL, PRIMARY KEY(date,source));
CREATE TABLE IF NOT EXISTS screen_time_sync(id INTEGER PRIMARY KEY CHECK(id=1), last_synced TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS screen_time_ideas(fingerprint TEXT PRIMARY KEY, ideas_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def connect() -> sqlite3.Connection:
    # Read paths off the config module at call time so tests can repoint them.
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _seed_schedule(conn)
    _seed_quotes(conn)
    conn.commit()


def _seed_schedule(conn):
    data = json.loads((config.SEED_DIR / "schedule.json").read_text())
    for c in data["courses"]:
        conn.execute(
            "INSERT OR IGNORE INTO courses(code, name, folder, start_week) VALUES(?,?,?,?)",
            (c["code"], c["name"], c.get("folder"), c.get("start_week")),
        )
    for s in data["sessions"]:
        # sessions has no natural UNIQUE key in the spec schema, so guard by hand.
        conn.execute(
            """INSERT INTO sessions(course_code, kind, class_no, weekday, start, end, room)
               SELECT :course_code, :kind, :class_no, :weekday, :start, :end, :room
               WHERE NOT EXISTS (SELECT 1 FROM sessions WHERE course_code=:course_code
                 AND kind=:kind AND weekday=:weekday AND start=:start)""",
            s,
        )


def _seed_quotes(conn):
    path = config.SEED_DIR / "quotes.json"
    if not path.exists():
        return
    for q in json.loads(path.read_text()):
        conn.execute(
            "INSERT OR IGNORE INTO quotes(text, author, source, custom) VALUES(?,?,?,0)",
            (q["text"], q["author"], q.get("source")),
        )
