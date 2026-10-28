# Journal feature: build contract

Date: 2026-10-26. Status: binding for both builders. Read `SPEC.md` (existing contract), `DESIGN.md` (visual system) and this file. Where this file and `SPEC.md` disagree about the Journal, this file wins. Nothing in this file changes existing behaviour of the Today, Progress, Mind or More screens, the `notes` table, `ai.py` or `jobs.py`, except the additions listed in section 8.

Words used with one meaning throughout:

- **WIB**: `Asia/Jakarta`, +07:00, no DST. Every timestamp the API returns is ISO 8601 with `+07:00`, seconds precision (`config.now().isoformat(timespec="seconds")`).
- **Journal day** of an instant `t`: `(t.astimezone(TZ) - timedelta(hours=5)).date()`. So 2026-10-27 04:59 WIB belongs to journal day 2026-10-26, and 05:00 starts 2026-10-27. Implemented once as `moments.journal_day(t: datetime) -> str` (returns `YYYY-MM-DD`). The client never computes journal days for logic; it only displays what the server returns.
- **Moment day D**: the journal day D, from `D 05:00` to `D+1 05:00` WIB.
- **Awake window**: `[window_start, window_end)` on calendar date D, both `HH:MM`.
- **MiB**: 1024 × 1024 bytes. "150 MB" in user-facing text means 150 MiB.

---

## 1. Data model

### 1.1 DDL

Append the block below to the existing `SCHEMA` string in `bridge/bridge/db.py` (after `job_runs`). It is additive only: no existing table is altered. `init_db()` already runs `conn.executescript(SCHEMA)` and every statement is `IF NOT EXISTS`, so existing databases gain the new tables on the next bridge start and fresh databases get everything at once. `PRAGMA foreign_keys=ON` is already set per connection in `db.connect()`; the `ON DELETE CASCADE` / `SET NULL` clauses below depend on it, so every new code path must open connections through `db.connect()`.

```sql
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
```

Transcripts are folded into `journal_media` (one transcript per audio or video item). There is no separate transcripts table.

### 1.2 Settings keys

`app_settings` holds exactly two keys in v1. A missing key means the default.

| key | default | rule |
|---|---|---|
| `moment_window_start` | `09:00` | `HH:MM`, `>= 05:00` |
| `moment_window_end` | `21:00` | `HH:MM`, `<= 23:59`, and at least 60 minutes after start |

The whole window always sits inside one journal day (05:00 to 23:59 of calendar date D), so "the ping for day D" is never ambiguous. A changed window applies to the next `moment_days` row created; a row that already exists keeps its snapshot.

### 1.3 Invariants the code must keep

- At most one `journal_entries` row with `kind='moment'` per `moment_date` (unique partial index).
- `moment_days.entry_id IS NOT NULL` means "day D is counted for the streak". Nothing else counts.
- `journal_media.src_path` is non-NULL only while `status='processing'`.
- A free entry may hold at most 10 media rows. A moment entry holds exactly one media row (photo or video).
- No SQLite write transaction is held open while a subprocess (ffmpeg, ffprobe, sips, whisper) runs.

---

## 2. HTTP API

All routes are JSON unless stated, under the same origin as the PWA. Errors use FastAPI's existing shape `{"detail": "<human readable string>"}` (the PWA shows `detail` in a toast). The Journal routes live in `bridge/bridge/journal.py` as `router = APIRouter()` and are mounted in `app.py` with `app.include_router(journal.router)` placed before the static `app.mount("/")`. `journal.py`, `media.py`, `moments.py` and `push.py` never import from `app.py` (that would be circular); they open connections with `db.connect()` and close them in `try/finally`.

### 2.1 Route table

| Method | Path | Body / query | Returns | Errors |
|---|---|---|---|---|
| GET | /api/journal | `?limit=30&before_id=<int>&kind=free\|moment` (all optional; limit 1..100) | `{entries: [Entry], next_before_id: int\|null}` newest first (by id desc) | 422 bad params |
| POST | /api/journal/entries | `{text?: str}` (≤ 20000 chars, default `""`) | Entry (201), `kind='free'` | 422 |
| GET | /api/journal/entries/{id} | | Entry | 404 |
| PATCH | /api/journal/entries/{id} | `{text: str}` (≤ 20000) | Entry | 404, 422 |
| DELETE | /api/journal/entries/{id} | | 204; deletes media rows and files; for a moment entry also un-counts the day | 404 |
| POST | /api/journal/entries/{id}/media | multipart, field `file` (one file per request) | Media (201) | 404, 409, 413, 415, 422, 503 |
| GET | /api/journal/media/{id} | | Media | 404 |
| DELETE | /api/journal/media/{id} | | 204 | 404, 409 (media of a moment entry: replace the Moment instead) |
| POST | /api/journal/media/{id}/retranscribe | | Media (202), transcript status back to `pending` | 404, 409 (photo, or transcript status is `pending` or `none`) |
| GET | /api/journal/moment | | MomentState for the current journal day (creates the day's row if missing) | |
| POST | /api/journal/moment | multipart, field `file` (required), field `caption` (optional, ≤ 20000) | MomentState (201) | 409, 413, 415, 422, 503 |
| GET | /api/journal/moments | `?days=30` (1..400) | `{days: [MomentDay]}` oldest first | 422 |
| GET | /api/journal/settings | | Settings | |
| PUT | /api/journal/settings | `{window_start: "HH:MM", window_end: "HH:MM"}` | Settings | 422 |
| GET | /api/push/public-key | | `{public_key: "<base64url, no padding, 65-byte uncompressed P-256 point>"}` | |
| POST | /api/push/subscribe | `PushSubscription.toJSON()`: `{endpoint, expirationTime?, keys: {p256dh, auth}}` | `{id}` (201); upsert by endpoint | 422 |
| POST | /api/push/unsubscribe | `{endpoint}` | 204 (also when unknown) | 422 |
| POST | /api/push/test | | `{sent: int, failed: int, removed: int}` | |
| GET, HEAD | /journal-media/{name} | | the file, with Range support | 404 |

`GET /api/status` gains one key, `journal`: `{ffmpeg: bool, whisper: bool, push_subscriptions: int}`. `ffmpeg` is true when both `FFMPEG_BIN` and `FFPROBE_BIN` exist as files. `whisper` is true when `WHISPER_BIN` and `WHISPER_MODEL` both exist as files.

Status code meanings for the Journal routes:

| Code | When |
|---|---|
| 404 | entry or media id unknown; media file name invalid or missing |
| 409 | Moment not open yet (`"Your Moment is not open yet."`); media upload to a moment entry; media delete on a moment entry; more than 10 attachments (`"An entry holds at most 10 attachments."`); retranscribe in a wrong state |
| 413 | body over the limit (header pre-check, streamed count, or per-kind limit) |
| 415 | magic bytes are not an accepted type; audio sent to the Moment endpoint |
| 422 | validation; video longer than 60 s; audio longer than 600 s; ffprobe cannot read the file; photo conversion failed |
| 503 | `ffmpeg` or `ffprobe` binary missing when a video or audio file needs probing (`"ffmpeg is not installed on the Mac."`) |

Push subscribe validation: `endpoint` must parse as an `https` URL whose hostname is, or ends with `.` plus, one of `push.apple.com`, `fcm.googleapis.com`, `push.services.mozilla.com`, `notify.windows.com`; otherwise 422. This stops the bridge from POSTing to arbitrary hosts.

### 2.2 JSON shapes

**Entry**

```json
{
  "id": 12,
  "kind": "free",
  "day": "2026-10-26",
  "created_at": "2026-10-26T14:03:11+07:00",
  "updated_at": "2026-10-26T14:05:40+07:00",
  "text": "Walked to campus, thought about the exam.",
  "moment": null,
  "media": [Media, ...]
}
```

For `kind='moment'`, `moment` is `{"date": "2026-10-26", "submitted_at": "2026-10-26T14:40:02+07:00", "on_time": true}`, taken from the `moment_days` row. `media` is ordered by `position`.

**Media**

```json
{
  "id": 40,
  "entry_id": 12,
  "position": 0,
  "kind": "video",
  "status": "ready",
  "error": null,
  "url": "/journal-media/3f2c...e1.mp4",
  "thumb_url": "/journal-media/3f2c...e1_thumb.jpg",
  "poster_url": "/journal-media/3f2c...e1_poster.jpg",
  "width": 720,
  "height": 1280,
  "duration_ms": 14230,
  "bytes": 5234123,
  "transcript": {"status": "done", "text": "Oke jadi hari ini ...", "language": "id", "error": null},
  "created_at": "2026-10-26T14:03:15+07:00"
}
```

- `url`, `thumb_url`, `poster_url` are `null` while the file does not exist (`status='processing'` or `'error'`, or an optional derivative failed).
- Photo: `poster_url`, `width`, `height`, `duration_ms` are `null`, `transcript` is `null`.
- Audio: `thumb_url`, `poster_url`, `width`, `height` are `null`.
- `transcript.status` is one of `pending`, `done`, `error`, `none` (`none` = video without an audio track, or encode failed). `text` is `""` when whisper heard nothing.
- `width`/`height` are deliberately not given for photos: `sips` keeps the EXIF orientation tag, so stored pixel sizes can be rotated relative to what the browser shows. The frontend must not use them for photo aspect ratios.

**MomentState**

```json
{
  "date": "2026-10-26",
  "state": "pinged",
  "window": {"start": "09:00", "end": "21:00"},
  "ping_at": "2026-10-26T14:32:07+07:00",
  "on_time_until": "2026-10-26T15:02:19+07:00",
  "closes_at": "2026-10-27T05:00:00+07:00",
  "submitted_at": null,
  "on_time": null,
  "push_result": "sent",
  "entry": null,
  "streak": StreakState
}
```

- `state` is one of `no_ping_yet`, `pinged`, `submitted_on_time`, `submitted_late` for the current day. (`missed` only appears in MomentDay, for past days.)
- While `state == "no_ping_yet"`, `ping_at`, `on_time_until` and `push_result` are `null`. The server must never send the real `ping_at` before it has passed, in any response (these responses are cached by the service worker).
- `window` is the row's snapshot. `closes_at` is `D+1 05:00`.
- `on_time_until` = reference time + 30 min (reference defined in 6.2), present whenever `state != "no_ping_yet"`.
- `entry` is the full Entry of the Moment when submitted, else `null`.

**StreakState**

```json
{"current": 4, "best": 9, "today_counted": false, "last_counted": "2026-10-25"}
```

`last_counted` is the latest counted journal day or `null`.

**MomentDay** (history item)

```json
{"date": "2026-10-25", "state": "submitted_late", "on_time": false, "entry_id": 31, "thumb_url": "/journal-media/...._thumb.jpg"}
```

`state` is one of `no_ping_yet`, `pinged`, `submitted_on_time`, `submitted_late`, `missed`. `missed` means a past journal day with no counted Moment, including days with no `moment_days` row at all (bridge off all day). `thumb_url` is the Moment's photo thumb or video poster thumb, else `null`. The list starts at the later of `today - days + 1` and the earliest `moment_days.date`; it ends at today. With no rows at all it is empty.

**Settings**

```json
{"window_start": "09:00", "window_end": "21:00", "applies_from": "2026-10-27"}
```

`applies_from` is today's journal day if today's `moment_days` row does not exist yet, otherwise tomorrow's.

---

## 3. Upload protocol

### 3.1 Limits (constants in `bridge/bridge/media.py`)

| Constant | Value |
|---|---|
| `MAX_UPLOAD` | 150 MiB (whole request body pre-check, plus 1 MiB multipart overhead allowance) |
| `MAX_PHOTO` | 30 MiB |
| `MAX_AUDIO` | 50 MiB |
| `MAX_VIDEO` | 150 MiB |
| `MAX_VIDEO_SECONDS` | 60 (reject when probed duration > 61.0 s; encode always trims with `-t 60`) |
| `MAX_AUDIO_SECONDS` | 600 (reject when probed duration > 601.0 s; encode trims with `-t 600`) |
| `MAX_MEDIA_PER_ENTRY` | 10 |

### 3.2 Request handling, in order

1. **Header pre-check (middleware in `app.py`).** Extend the existing `limit_proof_upload` middleware (keep the `/proof` branch exactly as is). For `POST` requests whose path matches `^/api/journal/entries/\d+/media$` or equals `/api/journal/moment`, return `413 {"detail": "File larger than 150 MB."}` when `Content-Length > media.MAX_UPLOAD + 1 MiB`. Read `media.MAX_UPLOAD` through the module at call time (`from . import media`, never `from .media import MAX_UPLOAD`) so tests can monkeypatch it. Be honest about what this buys: Starlette parses the multipart body and spools the file part to the system temp directory (`/var/folders/...`, `SpooledTemporaryFile`, 1 MiB in memory) before the handler runs, so this header check is the only guard before that spool. A missing `Content-Length` (chunked body) on these two paths returns `411 {"detail": "Length required."}`, because Starlette would spool the whole file to the system temp directory before step 3 could count it; XHR with FormData always sends the header. The same middleware returns `403 {"detail": "Cross-site requests are not allowed."}` for any non-GET/HEAD request with `Sec-Fetch-Site: cross-site` (CSRF guard; a missing header, `same-origin`, `same-site` and `none` pass, so curl and Shortcuts keep working). Step 3 still counts a body whose `Content-Length` understates its size.
2. **Route handler** is a plain `def` (threadpool), parameter `file: UploadFile = File(...)` (and `caption: Optional[str] = Form(None)` on the Moment route). The client file name and the client `Content-Type` are ignored entirely.
3. **Streamed copy.** Copy `file.file` in 1 MiB chunks to `JOURNAL_TMP/<uid>.upload` (`uid = uuid.uuid4().hex`, generated server-side), counting bytes; at more than `MAX_UPLOAD` bytes stop, delete the partial file, return 413. Never call `.read()` without a size.
4. **Sniff** the first 16 bytes with `media.sniff(head) -> str | None`:

   | Bytes | Result |
   |---|---|
   | `FF D8 FF` at 0 | `jpg` |
   | `89 50 4E 47` at 0 | `png` |
   | `RIFF` at 0 and `WEBP` at 8 | `webp` |
   | `ftyp` at 4 and bytes 8..12 in `{heic, heix, mif1, msf1, hevc}` | `heic` |
   | `ftyp` at 4, any other brand | `isobmff` (mp4, mov, m4a; decided by ffprobe) |
   | `1A 45 DF A3` at 0 | `webm` |
   | `OggS` at 0 | `ogg` |
   | anything else | `None` → 415 `"Not a supported photo, video or audio file."` |

   Do not add video brands to the HEIC set. `app.sniff_ext` (task proofs) stays unchanged.
5. **Classify.** `jpg/png/webp/heic` → photo (size > `MAX_PHOTO` → 413). `isobmff/webm/ogg` → run ffprobe (4.2). A stream with `codec_type == "video"` and `disposition.attached_pic != 1` → video; otherwise an `audio` stream → audio; otherwise 422 `"No playable audio or video found."`. ffprobe failure → 422 `"Could not read this file."`. Missing binary → 503. Video over `MAX_VIDEO` or audio over `MAX_AUDIO` → 413. Duration over the cap → 422 (`"Videos can be at most 60 seconds."`, `"Voice notes can be at most 10 minutes."`). If ffprobe reports no duration (MediaRecorder WebM often has none), accept and rely on `-t` trimming.
6. **Process.**
   - Photo: run the photo pipeline (4.3) synchronously in the request. Success → insert the media row with `status='ready'`, return 201. Failure → 422 `"Could not process this photo."`, all files removed, no row.
   - Video or audio: insert the row with `status='processing'`, `transcript_status='pending'`, `src_path` = the upload path, return 201 immediately. The worker (4.5) does the rest.

On every error path, the upload file in `JOURNAL_TMP` is deleted before responding.

### 3.3 Free entry protocol (client side)

1. `POST /api/journal/entries {text}` creates the entry (text may be `""` when attachments follow).
2. For each attachment, sequentially: `POST /api/journal/entries/{id}/media` with `FormData` field `file`.
3. If a step fails, the composer keeps the created `entry_id`, the unsent attachments and their failure reason. Pressing Save again PATCHes the text if it changed and retries only the attachments not yet uploaded, into the same entry.
4. The worker deletes free entries whose text is `""` and that have no media rows, once they are older than 6 hours (cleanup inside the scheduler tick), so abandoned empty entries disappear.

### 3.4 What is immediate and what is asynchronous

| Kind | In the 201 response | Later |
|---|---|---|
| Photo | `status: "ready"`, `url`, `thumb_url` | nothing |
| Video | `status: "processing"`, all URLs `null`, `transcript.status: "pending"` | encode → `ready` with `url`, `poster_url`, `thumb_url`, `width`, `height`, `duration_ms`; then transcript `pending` → `done` / `error`, or `none` when no audio track |
| Audio | `status: "processing"`, `transcript.status: "pending"` | transcode → `ready` with `url`, `duration_ms`; then transcript `done` / `error` |

**How the client learns it finished:** polling. While an Entry on screen has any media with `status == "processing"` or `transcript.status == "pending"`, the client calls `GET /api/journal/entries/{id}` every 3 seconds, for at most 10 minutes per entry, and stops when nothing is pending, when the route changes, or when the document is hidden (it resumes on `visibilitychange` to visible). There is no server push for processing progress.

### 3.5 Moment upload

`POST /api/journal/moment`, fields `file` and optional `caption`.

1. Ensure today's `moment_days` row (5.2). If `now < ping_at` → 409 `"Your Moment is not open yet."`. This check runs before any processing.
2. Steps 3 to 6 of 3.2, except: audio → 415 `"A Moment is one photo or one video."`.
3. Commit in one `BEGIN IMMEDIATE` transaction, in exactly this order (the unique index on `moment_date` is checked per statement, so the old entry must be gone before the new one is inserted):
   - re-read the row; `old = entry_id`;
   - if `old`: read the old entry's `text` (caption carry-over) and collect its media `file`, `thumb`, `poster` and `src_path`; then `DELETE FROM journal_entries WHERE id=old` (media cascade; the FK sets `moment_days.entry_id` to NULL; `submitted_at` and `on_time` stay);
   - insert the new `journal_entries` row (`kind='moment'`, `day = moment_date = D`, `text` = `caption` when the field was sent and non-empty, else the old entry's text when replacing, else `""`);
   - insert its media row;
   - `UPDATE moment_days SET entry_id=:new, submitted_at=COALESCE(submitted_at, :now), on_time=COALESCE(on_time, :on_time) WHERE date=:D`.

   If the transaction fails, roll back and unlink the new files; the old Moment is untouched.
4. After commit, unlink the old files. Return 201 MomentState.

FastAPI treats an empty optional form field as absent, so `caption` cannot clear a caption. The PWA never sends `caption` on replace; caption edits go through `PATCH /api/journal/entries/{id}`.

`ensure_day` commits its `INSERT OR IGNORE` before returning, so the later `BEGIN IMMEDIATE` never runs inside an open implicit transaction.

The Moment counts from the moment the upload is accepted, even while a video is still encoding. If the encode later fails, the Moment still counts and the UI shows the error with a Replace button.

### 3.6 Media file serving

`GET`/`HEAD /journal-media/{name}`, declared with `@router.api_route(..., methods=["GET", "HEAD"])` (`@app.get` alone does not answer HEAD).

- `name` must fully match `^[0-9a-f]{32}(_thumb|_poster)?\.(jpg|mp4|m4a)$`, else 404.
- `p = config.JOURNAL_DIR / name`; 404 unless `p.is_file()` and `p.resolve().parent == config.JOURNAL_DIR.resolve()`. `JOURNAL_TMP` is never servable.
- Return `FileResponse(p, media_type=..., headers={"Cache-Control": "private, max-age=31536000, immutable"})` with `image/jpeg`, `video/mp4` or `audio/mp4`. Names are never reused, so immutable caching is safe.
- **Range:** Starlette 1.9.0 (locked in `uv.lock`) `FileResponse` answers `Range: bytes=a-b` with `206 Partial Content`, `Content-Range` and `Accept-Ranges: bytes`, and an unsatisfiable range with 416. iOS Safari refuses to play `<video>` from a server without 206 support. This is a hard requirement verified by a test (9.1), not an assumption.
- This route is deliberately outside `/api/` and `/photos/` so the service worker never intercepts it (7.6).

---

## 4. Media pipeline

### 4.1 Paths, binaries, test seam

New attributes in `bridge/bridge/config.py`, defined like the existing ones and repointed in `tests/conftest.py`:

```python
JOURNAL_DIR = DATA_DIR / "journal"          # served files
JOURNAL_TMP = JOURNAL_DIR / "tmp"           # uploads and intermediates, never served
VAPID_KEY_PATH = DATA_DIR / "vapid_private.pem"
BACKGROUND = os.environ.get("BM_BACKGROUND", "1") != "0"   # scheduler + worker loops
```

`bridge/data/` is already gitignored, so no `.gitignore` change is needed.

`config.settings()` gains these fields. The launchd PATH is `/Users/nabils/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin` and has no `/opt/homebrew/bin`, so each default falls back to an absolute path, the same way `CLAUDE_BIN` does:

| Field | Env var | Default |
|---|---|---|
| `FFMPEG_BIN` | `FFMPEG_BIN` | `shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"` |
| `FFPROBE_BIN` | `FFPROBE_BIN` | `shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"` |
| `WHISPER_BIN` | `WHISPER_BIN` | `shutil.which("whisper-cli") or "/opt/homebrew/bin/whisper-cli"` |
| `WHISPER_MODEL` | `WHISPER_MODEL` | `None` (transcription reports "not configured") |
| `VAPID_SUBJECT` | `VAPID_SUBJECT` | `PUBLIC_URL` when it starts with `https://`, else `"mailto:bridge@boredom-maxxing.invalid"` |

`sips` is called as `/usr/bin/sips`. Env values in `bridge/.env` reach launchd through the existing `load_dotenv` call; the user sets `WHISPER_MODEL` there. Builders never read or write `bridge/.env`; they document the new variables in `bridge/.env.example`.

**Every subprocess call in `media.py` goes through one function**, the single seam tests monkeypatch:

```python
def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run without a shell; raise MediaError on missing binary, timeout or non-zero exit."""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise MediaError(f"{Path(args[0]).name} is not installed")
    except subprocess.TimeoutExpired:
        raise MediaError(f"{Path(args[0]).name} timed out")
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        raise MediaError(f"{Path(args[0]).name} failed ({r.returncode}): {tail[0][:200]}")
    return r
```

`subprocess.run` with `timeout` kills the child on expiry. Args are always lists; nothing is ever passed through a shell. `MediaError` messages are stored in `error` / `transcript_error`, capped at 300 characters.

### 4.2 ffprobe

```
FFPROBE_BIN -v error -print_format json -show_format -show_streams <file>      timeout 15 s
```

Parse with `json.loads(stdout)`. Duration = `float(format["duration"])` when present and finite, else `None`. Video stream = first `streams[i]` with `codec_type == "video"` and `disposition.get("attached_pic", 0) != 1`. Audio = first stream with `codec_type == "audio"`. Width/height come from probing the encoded output, where rotation is already applied.

### 4.3 Photo pipeline (synchronous, in the request)

Input `src = JOURNAL_TMP/<uid>.upload`. All outputs are JPEG.

1. `/usr/bin/sips -g pixelWidth -g pixelHeight <src>` (timeout 30 s). Parse the two integers from stdout lines `pixelWidth: N` and `pixelHeight: N`. Failure → photo error.
2. `/usr/bin/sips -s format jpeg -s formatOptions 85 [-Z 2000] <src> --out JOURNAL_TMP/<uid>.jpg` (timeout 30 s). Add `-Z 2000` **only** when `max(w, h) > 2000`; `sips -Z` upscales smaller images (verified on this Mac), so it must never be passed unconditionally. HEIC, PNG and WebP are all converted to JPEG by this one command.
3. Thumbnail: when `max(w, h) > 480`, `/usr/bin/sips -s format jpeg -s formatOptions 70 -Z 480 JOURNAL_TMP/<uid>.jpg --out JOURNAL_TMP/<uid>_thumb.jpg`; otherwise copy `<uid>.jpg` to `<uid>_thumb.jpg` with `shutil.copyfile`.
4. `os.replace` both into `JOURNAL_DIR` (same filesystem, atomic), unlink the upload, insert the row with `file=<uid>.jpg`, `thumb=<uid>_thumb.jpg`, `bytes` = size of `<uid>.jpg`.

Failure at any step: unlink the upload and every `JOURNAL_TMP/<uid>*` file, no DB row, 422. EXIF (including location) is kept; this is a private single-user store.

### 4.4 Video and audio commands

All ffmpeg calls start with `FFMPEG_BIN -nostdin -hide_banner -loglevel error -y`.

**Video encode** (timeout 300 s):

```
... -i <src> -map 0:v:0 -map 0:a:0? -t 60
    -vf scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2
    -c:v libx264 -preset veryfast -crf 23 -profile:v high -level:v 4.0 -pix_fmt yuv420p
    -c:a aac -b:a 128k -ac 2
    -movflags +faststart
    JOURNAL_TMP/<uid>.mp4
```

The scale filter fits the video in a 1280 × 1280 box without upscaling, with even dimensions. For 16:9 and 9:16 sources that is exactly 1280 × 720 or 720 × 1280 ("720p"). ffmpeg auto-applies the rotation metadata before the filter, so portrait iPhone clips come out portrait. `0:a:0?` makes audio optional. `libx264` is present in the installed Homebrew ffmpeg 9.0.1.

**Poster** (timeout 30 s), at `ss = min(1.0, duration_s / 2)` (0 when the duration is unknown):

```
... -ss <ss> -i JOURNAL_TMP/<uid>.mp4 -frames:v 1 -q:v 3 JOURNAL_TMP/<uid>_poster.jpg
```

Then the thumbnail from the poster with the same sips rule as 4.3 step 3. Poster or thumbnail failure is not fatal: the item still becomes `ready` with `poster` and `thumb` NULL.

**Audio transcode** (timeout 120 s). Every voice note is stored as AAC in MP4 (`.m4a`), because WebM or Ogg from desktop browsers does not play on iOS:

```
... -i <src> -vn -map 0:a:0 -t 600 -c:a aac -b:a 96k -ac 1 -movflags +faststart JOURNAL_TMP/<uid>.m4a
```

**Audio extraction for transcription** (timeout 120 s), from the final served file:

```
... -i JOURNAL_DIR/<file> -vn -map 0:a:0 -ac 1 -ar 16000 -c:a pcm_s16le JOURNAL_TMP/<uid>.wav
```

**Whisper** (timeout 600 s):

```
WHISPER_BIN -m <WHISPER_MODEL> -f JOURNAL_TMP/<uid>.wav -l auto -t 4 -np -nt -oj -of JOURNAL_TMP/<uid>.whisper
```

This writes `JOURNAL_TMP/<uid>.whisper.json`. Parse it with `media.parse_whisper(raw: bytes) -> tuple[str, str | None]`:

- decode with `raw.decode("utf-8", "replace")` (whisper can split multi-byte characters);
- `json.loads`; segments = `data.get("transcription") or []`; text = `" ".join(s.get("text", "").strip() for s in segments)`;
- drop segments whose stripped text fully matches `^[\[(][^\])]*[\])]$` (for example `[BLANK_AUDIO]`, `(music)`);
- collapse runs of whitespace; language = `(data.get("result") or {}).get("language")`;
- any parse error → `MediaError("could not read whisper output")`.

The exact JSON shape of whisper-cli `-oj` could not be verified on this Mac (the model is not installed yet). The parser must be defensive as above and be tested against a checked-in fixture (`bridge/tests/fixtures/whisper.json`) shaped as `{"result": {"language": "id"}, "transcription": [{"text": " Halo"}, {"text": " this is a test."}]}`. The manual checklist includes a real run.

If `WHISPER_MODEL` is unset, or either `WHISPER_BIN` or the model file does not exist, the worker sets `transcript_status='error'`, `transcript_error='Transcription is not configured on the Mac.'` without spawning anything. The user can call retranscribe after installing.

### 4.5 The worker (one job at a time)

`media.worker_loop()` is an `asyncio` task started in the lifespan when `config.BACKGROUND` is true:

```python
async def worker_loop():
    while True:
        try:
            did = await asyncio.to_thread(process_next)
        except Exception:
            log.exception("journal worker step failed")
            did = False
        if not did:
            await asyncio.sleep(2)
```

Because the loop awaits each step, at most one encode or transcription runs at any time. Photos are processed in the request thread (4.3) and are not queued; `sips` on one image takes well under a second.

`process_next() -> bool` picks, in this priority:

1. `SELECT * FROM journal_media WHERE status='processing' ORDER BY id LIMIT 1` → encode job;
2. else `SELECT * FROM journal_media WHERE status='ready' AND transcript_status='pending' ORDER BY id LIMIT 1` → transcription job;
3. else return False.

**Encode job** (video or audio). Read the row, close the read. Run the commands from 4.4 into `JOURNAL_TMP`, then probe the output. On success, `os.replace` the outputs into `JOURNAL_DIR`, then:

```sql
UPDATE journal_media SET status='ready', file=?, thumb=?, poster=?, width=?, height=?, duration_ms=?, bytes=?,
  transcript_status=?, src_path=NULL, updated_at=?
WHERE id=? AND status='processing'
```

`transcript_status` is `'pending'` when the output has an audio stream, else `'none'`. If `rowcount == 0` (the entry was deleted meanwhile), unlink the files just placed. Finally unlink `src_path`.

**Encode failure** (any `MediaError` or `OSError`):

```sql
UPDATE journal_media SET status='error', error=?, transcript_status='none', src_path=NULL, updated_at=? WHERE id=? AND status='processing'
```

Then unlink `src_path` and every `JOURNAL_TMP/<uid>*` file. The raw upload is not kept; the original is still in the phone's library.

**Transcription job.** Run extraction and whisper, parse, then:

```sql
UPDATE journal_media SET transcript_status='done', transcript_text=?, transcript_lang=?, transcript_error=NULL, updated_at=?
WHERE id=? AND transcript_status='pending'
```

On failure, set `transcript_status='error'` and `transcript_error=?` with the same guard. In a `finally` block, always unlink `<uid>.wav` and `<uid>.whisper.json`.

**`recover()`**, run once, synchronously, in the lifespan before `yield` (so before any request can write into `JOURNAL_TMP`), and only when `config.BACKGROUND` is true. It is not called from `worker_loop`:

- Every `status='processing'` row whose `src_path` file no longer exists → `status='error'`, `error='Upload was lost when the bridge restarted.'`, `transcript_status='none'`, `src_path=NULL`.
- Delete every file in `JOURNAL_TMP` that is not the `src_path` of a remaining `processing` row. These are leftovers of interrupted runs.
- `transcript_status='pending'` rows need nothing; they are picked up again.

### 4.6 Deletion

`DELETE /api/journal/entries/{id}` and `DELETE /api/journal/media/{id}`:

1. In one transaction: collect `file`, `thumb`, `poster` and `src_path` of the affected media rows. For a moment entry, run `UPDATE moment_days SET entry_id=NULL, submitted_at=NULL, on_time=NULL WHERE entry_id=?`. Then delete the rows (media cascade from the entry). Commit.
2. Unlink every collected path with `missing_ok=True`. On macOS, unlinking a file that ffmpeg is still reading is safe; the worker's guarded final `UPDATE` then affects 0 rows and it removes its own outputs.

A deleted Moment no longer counts. If it was today's, the user may submit again before `closes_at` and that becomes a new first submission, with a new `submitted_at` and `on_time`. Deleting an old Moment removes that day from the streak history; the UI warns about this (7.4).

### 4.7 Failure modes summary

| Failure | DB state afterwards | Files |
|---|---|---|
| Body too large (header) | nothing | nothing written by us (Starlette may not even spool) |
| Body too large (streamed) or wrong type | nothing | upload deleted |
| ffprobe fails or binary missing | nothing (422 / 503) | upload deleted |
| Photo sips fails | nothing (422) | all `<uid>*` deleted |
| Video or audio encode fails or times out | media `status='error'`, `error` set, `transcript_status='none'` | upload and partial outputs deleted |
| Poster or thumb fails | media `ready`, `poster`/`thumb` NULL | video kept |
| Whisper not configured, fails or times out | `transcript_status='error'`, `transcript_error` set | wav and json deleted |
| Bridge killed mid-encode | row stays `processing`; `recover()` reprocesses it if the upload exists, else marks it `error` | stale tmp outputs deleted by `recover()` |
| Entry deleted mid-encode | rows gone | worker removes its outputs after the 0-row update |
| Moment replacement transaction fails | old Moment and its row unchanged (rollback) | new files deleted |

---

## 5. Scheduler and Web Push

### 5.1 Decision: in-process asyncio loop

The scheduler is `moments.scheduler_loop()`, an `asyncio` task in the bridge process, started in the FastAPI lifespan next to the worker when `config.BACKGROUND` is true, and cancelled on shutdown. It is not a separate launchd job. The bridge is already `KeepAlive` and `RunAtLoad`, the ping needs sub-minute precision (launchd `StartCalendarInterval` has no random time and minute granularity), and exactly-once is enforced in SQLite, so a second process could not double-send anyway. The bridge must keep running as a single uvicorn process (no `--workers`), as it does today.

```python
async def scheduler_loop():
    while True:
        try:
            await asyncio.to_thread(tick)
        except Exception:
            log.exception("moment scheduler tick failed")
        await asyncio.sleep(30)
```

`asyncio.sleep` uses the monotonic clock, which on macOS does not advance while the Mac sleeps. After a wake, the next tick therefore comes at most about 30 s later, and `tick()` compares wall-clock time, so a missed ping is caught up automatically.

### 5.2 Picking the ping (`moments.ensure_day(conn, day, now) -> Row`)

Called by `tick()` and by both Moment endpoints. It is idempotent: `INSERT OR IGNORE` on the `date` primary key, then `SELECT`.

- `start_dt = D window_start`, `end_dt = D window_end` (WIB, calendar date D), from the current settings, snapshotted into the row.
- `lo = max(start_dt, now)` rounded up to the next whole second.
- If `lo < end_dt`: `ping_at = lo + rng.randrange(int((end_dt - lo).total_seconds()))` seconds.
- If `lo >= end_dt` (the row is created after the window ended, for example the Mac woke at 22:00): `ping_at = start_dt`. The Moment is open at once, no push is sent (`skipped_late`), and a submission is late but still counts.
- `rng` is the module attribute `moments._rng = random.SystemRandom()`, so tests can replace it.

In normal operation the first tick after 05:00 creates the row, so the ping is uniform over the whole window and was picked at 05:00. When the Mac sleeps through 05:00, the row is created on wake and the ping is uniform over the rest of the window. In both cases the ping is never in the past at the moment it is picked, except in the after-window case.

### 5.3 `tick(now=None)` and exactly-once

```
now = now or config.now()
D   = journal_day(now)
c = db.connect()
try:                      # sqlite3's own context manager commits but does not close
    row = ensure_day(c, D, now)
    purge_empty_entries(c, now)          # 3.3 step 4
    if row.fired_at is not None: return
    if now < ping_at(row): return
    if row.entry_id is not None:
        mark(c, D, now, 'skipped_submitted'); return
    if now >= window_end_dt(row):
        mark(c, D, now, 'skipped_late'); return
    subs = all push_subscriptions
    if not subs:
        mark(c, D, now, 'no_subscribers'); return
    # Claim before sending: a crash after this line loses the ping, but can never send it twice.
    claimed = UPDATE moment_days SET fired_at=:now, push_result='failed', push_attempts=push_attempts+1
              WHERE date=:D AND fired_at IS NULL ; commit
    if claimed.rowcount == 0: return
    results = push.send_all(c, moment_payload(D))      # no transaction held during HTTP
    if results.sent > 0:
        UPDATE moment_days SET push_result='sent' WHERE date=:D
    elif results.transient > 0 and now < window_end_dt(row) and row.push_attempts + 1 < 10:
        UPDATE moment_days SET fired_at=NULL, push_result=NULL WHERE date=:D   # release, retry next tick
    # else stays 'failed'
finally:
    c.close()
```

`mark()` is `UPDATE moment_days SET fired_at=?, push_result=? WHERE date=? AND fired_at IS NULL`.

Guarantees:

- **Never twice.** The claim is a conditional update. Only a claim with no successful delivery is released, and only for transient errors (5.5).
- **Late on wake.** If the Mac was asleep at `ping_at`, the first tick after wake sends it, provided `now < window_end`.
- **Never outside the window.** Outside the window there is no send: before `ping_at` nothing happens, and at or after `window_end` the row is marked `skipped_late`.
- **No ping after posting.** A Moment submitted between `ping_at` and the first tick sets `skipped_submitted`.
- A crash between claim and send loses that day's push. The Moment still opens at `ping_at`, because opening is time-based (6.1), so the user only loses the notification.

### 5.4 VAPID keys

- **Location:** `config.VAPID_KEY_PATH` = `bridge/data/vapid_private.pem` (PKCS8 PEM, P-256), gitignored with the rest of `bridge/data/`. Not `bridge/.env`: builders may not read or write that file, and a generated secret does not belong in a hand-edited file.
- **Creation:** `push.ensure_keys()` runs in the lifespan (always, also when `BACKGROUND` is off). If the file does not exist, it generates a key with `cryptography` (`ec.generate_private_key(ec.SECP256R1())`) and writes it with `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)`. It never overwrites an existing file.
- **Public key:** derived on each call from the private key: `public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)`, base64url without padding.
- **Never printed:** no log line, exception message, API response or test output contains the private key or its path's content. Exceptions from key loading are logged as `"VAPID key unreadable"` only.
- **Loss:** if `bridge/data/` is wiped, a new key is generated and old subscriptions stop working (the push service rejects them, and they are removed or fail). The client heals by resubscribing (7.5).

### 5.5 Sending (`push.py`)

Dependency: `pywebpush` (add to `bridge/pyproject.toml` `dependencies`; it pulls in `cryptography`, `py-vapid`, `http-ece` and `requests`). Run `uv lock` (network needed) so `bridge/uv.lock` is updated.

The single send seam tests monkeypatch:

```python
def _send(sub_info: dict, data: str) -> int:
    """Return the push service's HTTP status. Network errors (requests.RequestException) propagate."""
    try:
        r = pywebpush.webpush(
            subscription_info=sub_info, data=data,
            vapid_private_key=str(config.VAPID_KEY_PATH),
            vapid_claims={"sub": config.settings().VAPID_SUBJECT},   # fresh dict every call: pywebpush writes aud/exp into it
            ttl=3600, headers={"Urgency": "high"}, timeout=10)
    except pywebpush.WebPushException as e:
        # webpush() raises for any status above 202; turn that back into a status code.
        if e.response is None:
            raise
        return e.response.status_code
    return r.status_code
```

So `send_all` only ever sees an int or a raised network error, and test fakes (which return ints or raise `requests.ConnectionError`) behave like production. The builder confirms against the installed pywebpush source that `vapid_private_key` accepts a PEM file path, that the claims dict is mutated, and that `webpush()` raises `WebPushException` with `.response` for error statuses; the fresh-dict rule and the try/except stay either way.

`send_all(conn, payload: dict) -> Results(sent, failed, removed, transient)`:

1. Read all subscriptions and close the read (no transaction open).
2. Call `_send` for each subscription in turn, with no DB writes in between. A `WebPushException` without a response, or any other exception from one subscription, is caught and counted as transient for that subscription; it never stops delivery to the others.
3. Write all outcomes in one short transaction afterwards, then commit. No write lock is ever held during an HTTP call (invariant 1.3, and the default 5 s busy timeout would otherwise make concurrent uploads fail with "database is locked").

| `_send` outcome | Classification | Action |
|---|---|---|
| 2xx | sent | `last_ok_at=now`, `fail_count=0` |
| 404 or 410 | removed | delete the subscription row |
| 429 or 5xx, or a raised network error or timeout | transient | `fail_count += 1` |
| any other status (400, 403, 413) | failed | `fail_count += 1` |

`sent + failed + removed + transient` equals the number of subscriptions. The subscription keys are never logged; the endpoint is logged only as its hostname.

**Payloads** (JSON string as `data`):

- Moment: `{"title": "Time for your Moment", "body": "Post a photo or a short video in the next 30 minutes.", "url": "/#/journal", "tag": "moment-<D>"}`
- Test: `{"title": "Test notification", "body": "Moment notifications work.", "url": "/#/journal", "tag": "test"}`

### 5.6 Subscription endpoints

- `POST /api/push/subscribe`: validate (2.1), then `INSERT ... ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, user_agent=excluded.user_agent, fail_count=0`. `user_agent` is the request's `User-Agent` header, truncated to 200 characters. Returns 201 `{id}`.
- `POST /api/push/unsubscribe {endpoint}`: delete by endpoint; 204 whether or not it existed.
- `POST /api/push/test`: `send_all` with the test payload, returns the counts. It does not touch `moment_days`.

### 5.7 iOS limits (for the More screen copy and the risks)

- Web Push on iPhone works only for a site **added to the Home Screen** and opened from there, on **iOS 16.4 or later**.
- `Notification.requestPermission()` and `pushManager.subscribe()` must run inside a **user tap** handler in the installed app.
- Every push must end in a visible `showNotification` inside `event.waitUntil`; iOS revokes subscriptions that receive silent pushes.
- Delivery goes through Apple (`web.push.apple.com`): the Mac needs internet access, not only the tailnet, and Focus modes or Low Power Mode can delay or hide the banner. Deleting the Home Screen app deletes the subscription.

---

## 6. Moment rules and streak

### 6.1 State machine (current journal day D)

```
                  now >= ping_at                 upload accepted
  no_ping_yet ─────────────────────► pinged ─────────────────────► submitted_on_time | submitted_late
                                       │                                   │   ▲
                                       │ 05:00 D+1, nothing counted        │   │ replace (same D): state and on_time unchanged
                                       ▼                                   │   └───────
                                    missed (history only)                  │ delete entry
                                                                           ▼
                                                                         pinged (if before closes_at), or missed
```

Derivation, `moments.state_of(row, now)`:

```
if row.entry_id is not None: 'submitted_on_time' if row.on_time else 'submitted_late'
elif now < ping_at:          'no_ping_yet'
elif row.date == journal_day(now): 'pinged'
else:                        'missed'
```

- **Opening is time-based** (`now >= ping_at`), not delivery-based. A failed or skipped push never blocks the Moment.
- **Before the ping:** `POST /api/journal/moment` returns 409 `"Your Moment is not open yet."` and nothing is stored. The client shows no upload control in `no_ping_yet`, so this is only reachable by a stale screen.
- **Replacing:** allowed while `now < closes_at`. It swaps the entry and media and keeps the caption unless a new `caption` is sent. `submitted_at` and `on_time` keep the first submission's values, because the badge rewards showing up at the ping, and a replacement is for a bad shot.
- **After 05:00 D+1** the request targets the new journal day, which is normally `no_ping_yet`, so a late attempt for D gets 409. Day D can no longer change.

### 6.2 On time

`reference = fired_at if push_result == 'sent' else ping_at`. `on_time = submitted_at <= reference + 30 minutes`, computed at the first submission and stored. Using `fired_at` when the push was actually sent means a catch-up push after the Mac woke still gives a fair 30 minutes. `on_time` never affects the streak.

### 6.3 Streak algorithm (`moments.streak(conn, now) -> StreakState`)

```
today   = journal_day(now)
counted = { date for date in SELECT date FROM moment_days WHERE entry_id IS NOT NULL }   # all <= today

# current: today is still in play until 05:00 tomorrow, so an empty today does not break the run.
d = today if today in counted else today - 1 day
current = 0
while d in counted:
    current += 1
    d -= 1 day

# best: longest run of consecutive dates in counted
best, run, prev = 0, 0, None
for d in sorted(counted):
    run = run + 1 if prev is not None and d == prev + 1 day else 1
    best = max(best, run); prev = d

return {current, best, today_counted: today in counted, last_counted: max(counted) or None}
```

Edge cases, each with a test (9.1):

| Case | Result |
|---|---|
| No rows (first day) | `current 0`, `best 0`, `last_counted null` |
| Moment submitted at 2026-10-27 04:30 WIB | counts for 2026-10-26 |
| Moment submitted at 2026-10-27 05:00 WIB | belongs to 2026-10-27, and only if that day's ping has passed (else 409) |
| Today not yet posted, yesterday counted | `current` = run ending yesterday |
| Yesterday missed, today not yet posted | `current 0` |
| Yesterday missed, today posted | `current 1` |
| Moment replaced | still one counted day |
| Moment deleted | day no longer counted; `best` can shrink |
| Bridge off all day (no row) | that day is missed |
| `best >= current` | always |

All timestamps are WIB-aware `datetime`s from `config.now()`. Tests replace the clock by monkeypatching `config.now` and pass `now` explicitly to `tick`, `ensure_day`, `state_of` and `streak`.

---

## 7. PWA contract (frontend builder)

All code stays in `web/app.js`: the test harness `tests-web/motion.test.cjs` evaluates `app.js` as one script through `vm`, and the service worker's `SHELL` list is explicit, so no new JS module file. Visual design follows `DESIGN.md` (tokens, `.slab` at most once per screen, `.surface`, `.list`, `.disclose`, `.btn` variants, `.empty`, chips, toasts, 44 px targets). This section fixes behaviour, not looks. User-generated text (entry text, captions, transcripts) is inserted only through `h()` text nodes, never `innerHTML` or `md()`.

### 7.1 Route and tab

- Routes: `#/journal` (main screen) and `#/journal/<entry_id>` (entry detail). Register `journal: viewJournal` in `views` (the detail is `viewJournal(arg)` with an id argument). Add `journal: 'Journal'` to `TITLES`. The tab key is `journal` for both routes.
- Tab bar order: **Today, Progress, Mind, Journal, More**. Add `<a href="#/journal" data-tab="journal" aria-label="Journal">` with a new sprite symbol `i-journal` (2 px stroke, round caps, matching the sprite) and a `<span>Journal</span>`.
- Tab switches stay instant (no slide), like the existing tabs.

### 7.2 Journal screen (`#/journal`), top to bottom

1. **Title** "Journal".
2. **Moment block** (`GET /api/journal/moment`), rendered as its own node so it can be refreshed without touching the composer. Shows the streak (`streak.current`, `streak.best`) in every state.

   | `state` | Must show | Actions |
   |---|---|---|
   | `no_ping_yet` | "Your Moment comes at a random time between {window.start} and {window.end}." | none (no upload control) |
   | `pinged`, now < `on_time_until` | "Your Moment is open. On time until {HH:MM of on_time_until}." | primary "Post your Moment": `<input type="file" accept="image/*,video/*">` with no `capture`, inside a `label.btn` (the existing `.file-input` pattern) → `POST /api/journal/moment` |
   | `pinged`, now ≥ `on_time_until` | "Late, but it still counts until 05:00." | same button |
   | `submitted_on_time` | the Moment media (photo `thumb_url`, or the video player 7.4), caption, chip "On time" | "Replace" (neutral, same file input → same endpoint) while now < `closes_at`; link to `#/journal/<entry.id>` for caption edit or delete |
   | `submitted_late` | same, chip "Late" | same |

   Missed days: a 14-day strip from `GET /api/journal/moments?days=14`, one item per day with `aria-label` "{weekday day month}: on time / late / missed / not yet", for example "Sat 24 Oct: missed". If the day before today is `missed` and `streak.current == 0`, add the line "You missed yesterday. Your streak starts again today."

   Leaving `no_ping_yet` without knowing the hidden ping: while the Journal route is shown and the document is visible, re-fetch `GET /api/journal/moment` every 60 seconds. Re-fetch at once on a service-worker `push` message (7.5), on `visibilitychange` to visible, and on `online`. In `pinged`, set a timer for `on_time_until` that re-renders the Moment block, using the same pattern as `nextReveal`. Re-rendering the Moment block must never replace a `<video>` or `<audio>` that is playing (`!el.paused`).
3. **Notifications prompt** (7.5), shown only when notifications are not active.
4. **Composer** for a free entry, in a `.disclose` row "New entry" that opens in place:
   - a `textarea` (`aria-label="Entry text"`). Keyboard dictation works here with no extra code.
   - "Add photo or video": `<input type="file" accept="image/*,video/*" multiple>` in a `label.btn.neutral`.
   - "Record voice note" (7.3).
   - a list of pending attachments, each with type, size or duration, a preview (image object URL, `<audio controls>` for recordings), and a "Remove" button.
   - "Save" (primary) runs the protocol in 3.3 inside `run()`, so `busy` covers it. Progress text reads "Uploading 2 of 3 · 45%". Uploads use the `upload()` helper below. After full success: clear the composer, revoke object URLs, toast "Saved.", re-fetch the timeline.
   - Client pre-checks before uploading, with the same limits as the server: photo ≤ 30 MiB, video ≤ 150 MiB, recording ≤ 50 MiB, at most 10 attachments. Text or at least one attachment is required ("Write something or add an attachment.").
5. **Timeline** from `GET /api/journal?limit=30`, grouped by `entry.day` with a date heading per group (`fmtDate`). Each entry shows the time (`fmtStamp(created_at)`), a "Moment" chip and an "On time" or "Late" chip for moment entries, the text, media thumbnails or players, transcripts under audio and video, and a link to the detail. A "Load more" button fetches with `before_id=next_before_id` until `next_before_id` is null. Empty state: "No entries yet." / "Write a line, add a photo or record a voice note."

### 7.3 Voice recording

- On tap: `navigator.mediaDevices.getUserMedia({audio: true})`. On rejection, toast "Microphone access was denied. You can still type with the keyboard's dictation key."
- `mimeType`: the first of `['audio/mp4', 'audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']` where `MediaRecorder.isTypeSupported` is true; if none is supported, construct without options. iOS produces `audio/mp4` (AAC).
- `recorder.start(1000)`, collect `dataavailable` chunks, show the elapsed time. Stop automatically at 600 s.
- On stop: `new Blob(chunks, {type: recorder.mimeType})`, stop every track (releases the mic indicator), add it to the pending attachments with an `<audio controls>` preview. The filename sent is `voice` plus an extension; the server ignores it.
- If the document becomes hidden while recording, stop and keep what was recorded (iOS suspends capture in the background).
- Recording is impossible offline (the button is disabled), because the result could not be saved.

### 7.4 Entry detail (`#/journal/<id>`)

- `GET /api/journal/entries/{id}`; a 404 renders an empty state "Entry not found." with a back link.
- Back `.iconbtn` to `#/journal` (`aria-label="Back to Journal"`).
- A text `textarea` with "Save" → `PATCH /api/journal/entries/{id}`. For a moment entry, label it "Caption".
- Media list:
  - photos `<img src=url alt="Photo, {fmtStamp(created_at)}" loading="lazy">`;
  - video `<video controls playsinline preload="metadata" poster=poster_url src=url aria-label="Video, {m:ss}">`;
  - audio `<audio controls preload="metadata" src=url aria-label="Voice note, {m:ss}">`.
  - Below audio and video, the transcript block:

    | `transcript.status` | Shows |
    |---|---|
    | `pending` | "Transcribing…" |
    | `done` | the text, or "No speech detected." when it is `""` |
    | `error` | the error text, plus a "Try again" button → `POST /api/journal/media/{id}/retranscribe` |
    | `none` | nothing |

  - `status: "processing"`: "Processing video…" or "Processing voice note…". `status: "error"`: the error text.
  - Free entries: each media item has "Remove" → `confirm("Remove this attachment? It is deleted for good.")` → `DELETE /api/journal/media/{id}`.
- "Delete entry" (`.btn.danger`), using native `confirm()`:
  - free: "Delete this entry? Its photos, videos and voice notes are deleted for good."
  - moment: "Delete this Moment? It no longer counts toward your streak, and it is deleted for good."
  - then `DELETE /api/journal/entries/{id}`, toast "Deleted.", navigate to `#/journal`.
- Polling per 3.4 updates only the affected media item's node, never a playing element.

### 7.5 Notifications (Journal prompt and More)

Helper `pushState()` returns one of:

| Value | Condition | Copy |
|---|---|---|
| `unsupported` | no `serviceWorker`, `PushManager` or `Notification` | "This browser cannot receive notifications." |
| `install` | iOS (`/iPhone\|iPad/` in the user agent) and not standalone (`navigator.standalone !== true` and not `matchMedia('(display-mode: standalone)').matches`) | "Add BM to your Home Screen to get Moment notifications." |
| `denied` | `Notification.permission === 'denied'` | "Notifications are blocked. Turn them on in Settings > Notifications > BM." |
| `off` | permission `default`, or no subscription | button "Turn on Moment notifications" |
| `on` | permission `granted` and a subscription exists | nothing on Journal; "On" plus "Send test" in More |

Enable, inside the tap handler, in this order: `await Notification.requestPermission()` → if `granted`: `reg = await navigator.serviceWorker.ready`; `key = (await api('/api/push/public-key')).public_key`; `sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: b64urlToBytes(key)})`; `act('/api/push/subscribe', 'POST', sub.toJSON())`.

Heal on every Journal or More visit while online, when permission is `granted`:

- `getSubscription()` exists → compare `sub.options?.applicationServerKey` with the server key when the browser exposes it. On a mismatch, `unsubscribe()` and show `off`. Otherwise re-POST `/api/push/subscribe` (idempotent upsert).
- No subscription → show `off`. Do not auto-subscribe without a tap.

**More screen additions** (in the existing Setup list, as `.disclose` rows):

- "Moment notifications": the `pushState` copy, the enable button, and "Send test" → `POST /api/push/test` → toast "Sent to {sent} device(s)." or "No device received it."
- "Moment window": two `<input type="time">` fields with the labels "Earliest" and "Latest", plus "Save" → `PUT /api/journal/settings` → toast "Saved. Applies from {applies_from formatted}." Validation errors from the server show as toasts.
- The Bridge kv list gets "Transcription": "Ready" when `status.journal.whisper`, else "Not set up".

Page side of the service worker: `navigator.serviceWorker.addEventListener('message', ...)`, guarded with `?.` because the test harness stubs `navigator`:

- `{type: 'open', hash}` → `location.hash = hash`, where `hash` must start with `#/`;
- `{type: 'push'}` → re-fetch the Moment block if the Journal route is shown.

### 7.6 Service worker changes (`web/sw.js`)

- `SHELL_CACHE` → `'bm-shell-v7'`. `DATA_CACHE` stays `'bm-data-v1'`.
- Fetch: add an explicit early `return` (no `respondWith`) for `url.pathname.startsWith('/journal-media/')`, with a comment. A 206 is `ok` but `cache.put` rejects partial responses, iOS needs native Range handling for playback, and 150 MB videos must never enter Cache Storage. Uploads are `POST`s and are already skipped by the `method !== 'GET'` guard. `/api/journal*` and `/api/push/public-key` GETs keep the existing network-first behaviour, so the timeline and Moment state show offline from cache. They never contain the hidden ping or any key material.
- `push` handler:

```js
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { /* show a generic notification */ }
  const url = typeof d.url === 'string' && d.url.startsWith('/#/') ? d.url : '/#/journal';
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || 'Boredom-Maxxing', {
      body: d.body || '', tag: d.tag || 'bm', data: { url }, icon: '/icons/icon-192.png',
    }),
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(list => list.forEach(c => c.postMessage({ type: 'push', tag: d.tag || null }))),
  ]));
});
```

- `notificationclick` handler: `e.notification.close()`; inside `e.waitUntil`: find a window client, `focus()` it and `postMessage({type: 'open', hash: '#/journal'})`. If there is none, `clients.openWindow(<url from data>)`. Do not rely on `WindowClient.navigate`.

### 7.7 Upload helper

Add `upload(path, form, onProgress)` next to `act()`:

- It throws `Error('offline')` when `offline` is set.
- It uses `XMLHttpRequest`, because `fetch` has no upload progress and a 150 MB upload needs some.
- It reports `xhr.upload.onprogress` as a fraction.
- It resolves the parsed JSON on 2xx. Otherwise it rejects with `Error(detail)`, where `detail` comes from the JSON body when it is a string, else `Request failed (<status>)`, and sets `err.status`.
- A network error rejects with `Error('offline')` and calls `setOffline(true)`.
- Always call it inside `run()`.

### 7.8 App update safety

Extend the `unsaved` check in `applyUpdate()` so a service-worker update never reloads over Journal work. Add these three conditions to the existing note and appeal checks:

- the composer has text or pending attachments;
- a `MediaRecorder` is recording;
- an entry text or caption edit differs from its saved value.

The v7 bump makes this reload happen on the first deploy, so it must be in place before shipping.

### 7.9 Offline behaviour

- Reads: the Moment block, history strip and timeline come from `DATA_CACHE` when offline. The existing banner shows via `X-SW-Cache`.
- Every Journal mutation control is disabled offline: post or replace Moment, attach, record, Save, edit, delete, remove attachment, retry transcription, enable notifications, send test, and save window. Disclosure rows show the subline "Needs a connection", as elsewhere.
- Media elements: on an `error` event, replace the element with the text "Media needs a connection." Media is never cached by the service worker.
- The composer draft lives in memory only; it is not persisted to storage in v1.

### 7.10 Accessibility requirements

- Every interactive element has at least a 44 × 44 px hit area.
- The record button is a `<button>` with `aria-pressed`. Its label is "Record voice note" when idle and "Stop recording" while recording. The elapsed time is visible as text. Start and stop are announced through the existing `#toast` (`role="status"`): "Recording started." and "Voice note recorded, 0:42."
- Native `<audio controls>` and `<video controls playsinline>` with the `aria-label`s from 7.4. There are no custom play buttons in v1.
- Images have `alt` text as in 7.4. Moment media uses "Moment photo, {fmtStamp(submitted_at)}" or "Moment video, {m:ss}".
- Delete and remove buttons carry context in `aria-label`, for example "Delete entry from 26 Oct, 14:03".
- Upload progress is text, and is also announced at start and at end. It is not a progress bar alone.
- File inputs stay keyboard-focusable with the existing `.file-input` and `.btn:has(:focus-visible)` pattern.
- Reduced motion: no pulsing record indicator and no animated progress. A static dot plus text carries the state. All Journal motion goes through the existing `animate()` helper, which already respects `prefers-reduced-motion`.
- After saving an entry, focus moves to the new entry's heading. After deleting, focus moves to `#view`.

---

## 8. File ownership (zero overlap)

**BACKEND owns, and may touch only:**

| File | Change |
|---|---|
| `bridge/bridge/config.py` | edit: `JOURNAL_DIR`, `JOURNAL_TMP`, `VAPID_KEY_PATH`, `BACKGROUND`, new `settings()` fields (4.1) |
| `bridge/bridge/db.py` | edit: append the DDL (1.1) to `SCHEMA` |
| `bridge/bridge/app.py` | edit: lifespan (`push.ensure_keys()`; when `config.BACKGROUND`: `media.recover()` before `yield`, then start both loops, cancel them on shutdown), the upload middleware branch (3.2), `app.include_router(journal.router)` before the static mount, the `journal` key in `/api/status` |
| `bridge/bridge/journal.py` | new: `router`, Pydantic models, entry and media JSON, entry, media, moment, settings and push routes, `/journal-media/{name}`, deletion (4.6) |
| `bridge/bridge/media.py` | new: limits, `sniff`, `_run`, `MediaError`, `probe`, photo pipeline, encode and transcription jobs, `parse_whisper`, `process_next`, `recover`, `worker_loop` |
| `bridge/bridge/moments.py` | new: `journal_day`, settings get and put, `_rng`, `ensure_day`, `state_of`, `moment_state`, `history`, `submit`, `streak`, `tick`, `purge_empty_entries`, `scheduler_loop` |
| `bridge/bridge/push.py` | new: `ensure_keys`, `public_key_b64`, `subscribe`, `unsubscribe`, `_send`, `send_all`, payload builders |
| `bridge/pyproject.toml` | edit: add `pywebpush` to `dependencies` |
| `bridge/uv.lock` | regenerated by `uv lock` |
| `bridge/.env.example` | edit: commented `FFMPEG_BIN`, `FFPROBE_BIN`, `WHISPER_BIN`, `WHISPER_MODEL`, `VAPID_SUBJECT`, `BM_BACKGROUND` lines |
| `bridge/tests/conftest.py` | edit: repoint `JOURNAL_DIR`, `JOURNAL_TMP`, `VAPID_KEY_PATH`; set `config.BACKGROUND = False`; autouse guards (9.1) |
| `bridge/tests/test_journal_api.py`, `test_media.py`, `test_moments.py`, `test_push.py` | new |
| `bridge/tests/fixtures/whisper.json` | new |

Not touched by the backend: `ai.py`, `jobs.py`, `ics.py`, `materials.py`, `occurrences.py`, `ypt.py`, `launchd/*`, `scripts/*`, `seed/*`, the existing `/proof` code path, `bridge/.env`, and everything outside `bridge/`.

**FRONTEND owns, and may touch only:**

| File | Change |
|---|---|
| `web/index.html` | edit: the Journal tab link, the `i-journal` sprite symbol, and any other new sprite symbols (for example `i-mic`) |
| `web/app.js` | edit: `viewJournal`, composer, recorder, `upload()`, push helpers, More additions, router and `TITLES` entries, `applyUpdate` unsaved check, SW message listener, visibility and online refresh for Journal |
| `web/styles.css` | edit: Journal styles using the existing tokens |
| `web/sw.js` | edit: v7, `/journal-media/` pass-through, `push`, `notificationclick` |
| `tests-web/journal.test.cjs` | new |
| `tests-web/motion.test.cjs` | edit only where the new tab or Journal code breaks its stubs (for example adding `'journal'` to the stubbed tab list or a `navigator.serviceWorker` stub); no assertions removed |
| `DESIGN.md` | append only: a `### Journal (viewJournal)` section under Screens and new component notes. No existing text is changed |

Not touched by the frontend: `web/manifest.webmanifest`, `web/icons/*`, and everything under `bridge/`.

**Neither builder touches:** `SPEC.md`, `HANDOFF.md`, `docs/**` (including this file), `.gitignore`, `reference/`, `design/`. The orchestrator updates `SPEC.md` and `HANDOFF.md` after both builds merge.

The interface between the two builders is exactly sections 2, 3.3 to 3.6, 5.5 payloads and 6.1; neither side needs the other's code to build or test.

---

## 9. Test plan

### 9.1 Backend (pytest, `cd bridge && uv run pytest -q`)

Global rules, enforced in `conftest.py`:

- Use the existing `conn` fixture with `tmp_path` as `BM_DATA_DIR`. Repoint `JOURNAL_DIR`, `JOURNAL_TMP` and `VAPID_KEY_PATH` under `tmp_path`.
- Set `monkeypatch.setattr(config, "BACKGROUND", False)` so `TestClient(app)` starts no loops.
- An autouse fixture replaces `media._run` and `push._send` with functions that raise `AssertionError("real binary/push called")`. Tests that need them install fakes that record `args` lists and write the expected output files.
- No test spawns ffmpeg, ffprobe, sips or whisper, and none sends a real push or touches `bridge/data`.
- The clock: monkeypatch `config.now`, or pass `now=` explicitly. Randomness: replace `moments._rng` with an object whose `randrange(n)` returns a fixed value.

Required cases (one test function each, names indicative):

| Area | Case |
|---|---|
| schema | `init_db` twice is idempotent; inserting `kind='bogus'` fails; two moment entries for one date fail; a moment entry without `moment_date` fails |
| day | `journal_day` at 04:59:59 is the previous day and at 05:00:00 the same day |
| settings | defaults 09:00 to 21:00; PUT validates the format, start ≥ 05:00, end ≤ 23:59 and length ≥ 60 min; `applies_from` is today with no row and tomorrow with a row; an existing row keeps its snapshot |
| ensure_day | ping inside the window; idempotent (second call same `ping_at`); created at 13:00 picks ≥ 13:00; created at 22:00 gives `ping_at = start` |
| state | each of `no_ping_yet`, `pinged`, `submitted_on_time`, `submitted_late`; `ping_at`, `on_time_until` and `push_result` are null in `no_ping_yet` (also check the raw response text does not contain the ping time) |
| moment submit | 409 before the ping with nothing stored; on time at +29 min; late at +31 min; reference is `fired_at` when `sent`; at 04:30 D+1 it counts for D; audio returns 415 |
| replace | succeeds against the unique `moment_date` index (old entry deleted before the new insert); keeps `submitted_at`, `on_time` and the caption when no caption field is sent; deletes the old entry and files; still one counted day |
| delete | deleting a Moment clears `submitted_at` and `on_time`, the day is un-counted, and resubmitting is a new first submission; free entry delete removes all files from disk |
| streak | every row of the 6.3 edge-case table |
| history | `missed` for a past day without a row; starts at the first row; today's hidden ping is not leaked |
| tick | fires once over two ticks (one `_send` call); catch-up after "sleep" (now = ping + 3 h, before window end) sends; now ≥ window end gives `skipped_late` and no send; submitted first gives `skipped_submitted`; no subscriptions gives `no_subscribers`; a transient error releases the claim and the next tick retries; a permanent 403 stays `failed` with no retry; 410 deletes the subscription; a pre-set `fired_at` means no send |
| push keys | `ensure_keys` creates the file with mode `0o600` and never overwrites it; the public key is 65 bytes after decoding and starts with `0x04`; the PEM text appears in no response body and in no `caplog` record |
| push API | subscribe upsert is idempotent (same id); a non-push host gives 422; an `http://` endpoint gives 422; unsubscribe of an unknown endpoint gives 204; test returns counts with the fake `_send`; one subscription raising `requests.ConnectionError` does not stop delivery to the next |
| push seam | with `pywebpush.webpush` monkeypatched (not `_send`): raising `WebPushException` with a 410 response makes `_send` return 410; the claims dicts passed on two calls are distinct objects |
| upload guards | a text file returns 415; a `Content-Length` header over the limit returns 413 before the handler; a streamed oversize returns 413 (monkeypatch `MAX_UPLOAD` small, send a chunked body) and leaves no tmp files; client filename `../../evil.jpg` is ignored (stored name matches the uid pattern); 11th attachment returns 409; upload to a moment entry via the media route returns 409 |
| photo | the sips call sequence is list args, with `-Z 2000` only when the fake reports more than 2000 px, and none for 1200 px; a HEIC magic header is accepted; sips failure returns 422 and leaves no files and no row |
| video/audio | 201 with `processing`; a fake ffprobe with 75 s returns 422; no duration is accepted; `process_next` encodes, giving `ready` with poster and thumb, `transcript_status` `pending` with audio and `none` without; ffmpeg failure gives `error` and cleans tmp; timeout (`MediaError` from the fake) gives `error`; jobs run strictly one at a time and encodes come before transcriptions (assert the order of recorded calls) |
| transcription | parses `fixtures/whisper.json` to text and language; `[BLANK_AUDIO]` is dropped; invalid UTF-8 does not crash; model unset gives `error` "not configured" with no `_run` call; retranscribe resets to `pending`; retranscribe on a photo returns 409 |
| race | deleting an entry while its media is `processing`, then running `process_next`, leaves no files in `JOURNAL_DIR` |
| recover | a `processing` row with a missing `src_path` becomes `error`; orphan tmp files are removed and referenced ones are kept |
| serving | a valid name returns 200 with the right media type; `Range: bytes=0-99` returns **206** with `Content-Range: bytes 0-99/<size>` and `Accept-Ranges: bytes`; HEAD returns 200; `..%2Fapp.sqlite3`, `x.jpg`, uppercase hex and a tmp file name all return 404 |
| status | `/api/status` includes `journal` with the three keys |
| regression | the existing 60 tests still pass unchanged |

### 9.2 Frontend (`node --check web/app.js web/sw.js`, `node tests-web/motion.test.cjs`, `node tests-web/journal.test.cjs`)

`tests-web/journal.test.cjs` uses `node:test` and a `vm` harness modelled on `motion.test.cjs`, with `api` stubbed by fixture JSON shaped exactly as in 2.2. Required cases:

- Each MomentState (`no_ping_yet`, `pinged` before and after `on_time_until`, `submitted_on_time`, `submitted_late`) renders its required text. The upload input exists only in `pinged` and the submitted states. A `missed` history day renders its label.
- Entry text, caption and transcript containing `<img src=x onerror=alert(1)>` render as text: no element is created from them.
- Offline (`setOffline(true)`) disables every Journal mutation control listed in 7.9.
- `applyUpdate()` does not reload while the composer has text, has a pending attachment, or a recorder is active.
- `upload()` rejects with the server `detail` on 413 and with `offline` on a network error.
- Router: `#/journal` and `#/journal/12` both mark the `journal` tab `aria-current`.
- In a `vm` context for `sw.js`:
  - `SHELL_CACHE === 'bm-shell-v7'`;
  - a GET to `/journal-media/<uid>.mp4` does not call `respondWith`, and neither does a POST to `/api/journal/moment`;
  - `push` calls `showNotification` inside `waitUntil` with the payload title and with the default URL when the payload URL is not `/#/...`;
  - a `push` with no or invalid data still shows a notification;
  - `notificationclick` focuses an existing client and posts `{type: 'open', hash: '#/journal'}`, or calls `openWindow` when there is none.
- `motion.test.cjs` still passes.

### 9.3 Manual iPhone checklist (after deploy and `launchctl kickstart -k gui/$(id -u)/com.boredommaxxing.bridge`)

1. Kill and reopen the Home Screen app twice. The new tab appears (SW v7) and nothing typed was lost.
2. More > Moment notifications > turn on. The permission prompt appears only after the tap. Then "Send test": a banner arrives with the app in the background and with it closed. Tapping it opens Journal.
3. Set the window to the next 60 minutes. The next day (or after deleting today's row on a test DB), the ping arrives inside the window, once.
4. Sleep the Mac across the ping time, wake it before the window end: one late push arrives. Wake it after the window end: no push, and the Moment shows as open and late.
5. Post a Moment photo from the library (no camera sheet forced). Post a **Live Photo**: it arrives as a still and is accepted. Then replace it: the badge stays the same.
6. Post a 50 s **video** from the library: it shows "Processing", then plays inline with sound and scrubbing (proves Range). Try a 70 s video: you get the 60-second message. Try an **HDR** clip and note whether the colours look washed out.
7. Record a 20 s voice note mixing **Indonesian and English**: it plays back on iPhone, and the transcript appears within about a minute. Check its quality and the detected language.
8. Type an entry with keyboard dictation. Add 3 photos and 1 video in one entry: the progress text advances, and all 4 items show.
9. Edit the text and save. Remove one attachment. Delete the entry: the confirm dialog appears and the files are gone (`ls bridge/data/journal`).
10. Airplane mode: Journal shows cached text, the offline banner shows, all mutation buttons are disabled, and media shows "Media needs a connection."
11. VoiceOver: the record button announces its state, the players have labels, the delete buttons say which entry, and the streak strip days read their state.
12. Reduce Motion on: no pulsing recorder, nothing animates.
13. Streak: post on two consecutive days and check that current is 2. Skip a day and check that current is 0 and best is 2.

---

## 10. Decisions made on the user's behalf, and open risks

### 10.1 Decisions

| # | Decision | Reason |
|---|---|---|
| D1 | The scheduler is an in-process asyncio loop with a 30 s tick, not a launchd job | The bridge is already KeepAlive; launchd cannot do random sub-minute times; exactly-once lives in SQLite anyway. |
| D2 | Claim-before-send for the ping, with release only on transient-only failures | "Never twice" outranks "never lost"; the Moment still opens at `ping_at` even if the push is lost. |
| D3 | The Moment opens at `ping_at` (time-based); posting earlier returns 409 | Keeps the surprise; a failed push never blocks the streak. |
| D4 | A late-created day row picks the ping from the rest of the window; after the window, `ping_at = window start` with no push | Satisfies "late on wake, never outside the window" without a burst of instant pings at wake. |
| D5 | On time is measured from the actual send time when the push was sent, else from `ping_at` | A catch-up push after sleep still gives a fair 30 minutes. |
| D6 | A replacement keeps the first `submitted_at` and `on_time` | The badge rewards showing up; replacement is for a bad shot. |
| D7 | A Moment counts from upload acceptance, even if the video encode later fails | The user did the act; server failures must not break the streak. |
| D8 | Deleting a Moment un-counts its day (and can lower best) | "Derived from data" means deleted data is gone; the confirm dialog says so. |
| D9 | The journal day (05:00 boundary) is used for both Moments and free-entry grouping | One day definition everywhere, matching YPT and the existing 05:00 rule. |
| D10 | Transcripts are folded into `journal_media` | One transcript per audio or video item; fewer joins and no orphans. |
| D11 | Photos are processed synchronously; video and audio go through a one-job queue | sips is sub-second and the client gets a URL at once; ffmpeg and whisper are slow and need the concurrency limit. |
| D12 | All photos are normalised to JPEG ≤ 2000 px, voice notes to AAC `.m4a`, video to H.264 MP4 | Everything plays on iOS Safari; one served format per kind. |
| D13 | Photo width and height are not exposed | sips keeps the EXIF orientation, so stored sizes can be rotated relative to the display. |
| D14 | The media route is `/journal-media/` outside `/api/`, with immutable caching and no SW interception | Native Range playback, nothing large in Cache Storage. |
| D15 | VAPID key is in `bridge/data/vapid_private.pem` (0600), auto-generated | Gitignored runtime dir; builders may not touch `bridge/.env`. |
| D16 | Push endpoint hosts are allow-listed | Prevents the bridge from POSTing to arbitrary URLs. |
| D17 | One file per media request; entries are created first | Per-file progress, simple limits, retry of only the failed files. |
| D18 | Upload uses XHR, not fetch | fetch has no upload progress; 150 MB needs it. |
| D19 | The Journal tab goes fourth, before More (Today, Progress, Mind, Journal, More) | More stays last by iOS convention. |
| D20 | Notifications need an explicit tap and are never auto-subscribed | iOS requires a user gesture; it also avoids surprise prompts. |
| D21 | No missed-day freeze, no AI on journal content, no persisted drafts, no transcript editing in v1 | Out of scope per the requirements, or not requested. |
| D22 | Video longer than 60 s is rejected, not trimmed; a missing duration is accepted and trimmed | Clear feedback for normal clips; WebM from MediaRecorder lacks a duration. |
| D23 | Video is fitted into a 1280 × 1280 box, never upscaled | Exactly 720p for 16:9 and 9:16 phone video; avoids huge files from 4K. |
| D24 | Empty free entries older than 6 h are purged by the tick | Cleans up after an abandoned composer save. |

### 10.2 Open risks

| Risk | Impact | Mitigation in v1 |
|---|---|---|
| iOS Web Push reliability (Focus, Low Power, delayed delivery, subscription lost on app removal) | ping missed or late | The Moment opens by time; 60 s polling while Journal is open; the test button in More |
| whisper-cli `-oj` JSON shape not verified (model not installed yet) | transcripts error | defensive parser plus fixture; the retranscribe endpoint; manual check 9.3.7 |
| Indonesian and English code-switching with `-l auto` picks one language per clip | weaker transcripts | accepted for v1; `transcript_lang` is stored so a later fix can retry with `-l id` |
| iPhone HDR (10-bit HLG) video converted to 8-bit SDR without tone mapping | washed-out colours | accepted; manual check 9.3.6; tone mapping is a later change |
| Starlette spools the whole multipart body to `/var/folders` before the handler, then we copy it | up to about 300 MB of temporary disk per upload, plus the time to write it | header pre-check rejects oversize early; single user |
| Disk growth: no quota or backup for `bridge/data/journal/` | the Mac disk fills over months | `bytes` stored per item, so a usage readout is easy to add later |
| Mac asleep or off: the bridge is unreachable, so the Moment cannot be posted | streak loss | accepted; posting a Moment needs a running bridge by design |
| VAPID key loss (data dir wiped) | all subscriptions invalid | the client re-subscribes on the next tap; old rows are removed on 404 or 410, or keep failing |
| MediaRecorder on iOS may yield odd MP4 timing | wrong duration or a failed transcode | the server re-encodes and probes the output; error state plus Replace or re-record |
| QuickTime files without a leading `ftyp` box | rejected with 415 | iPhone exports always start with `ftyp qt`; revisit if it happens |
| EXIF (including GPS) kept in stored photos | location data on the Mac disk | single-user private store, served only over the tailnet |
| Five tabs in the floating pill at 390 px width (active tab shows its label) | crowded or overlapping tab bar | frontend builder checks at 375 and 390 px and adjusts per DESIGN.md; not verified here |
| A single uvicorn process is assumed for the loops | duplicate loops if someone adds `--workers` | exactly-once is still enforced by the conditional updates |
