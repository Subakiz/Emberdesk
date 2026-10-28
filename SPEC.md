# Boredom-Maxxing v1 build spec

Contract for all builders.
Personal use, one user, reachable only over Tailscale. Timezone: `Asia/Jakarta` (WIB, +07:00, no DST) everywhere; use `zoneinfo`, never naive server-local dates.

## Layout

```
bridge/                     Python package `bridge` (run from repo root: `uv run --project bridge ...`)
  pyproject.toml            deps: fastapi, uvicorn[standard], python-multipart, python-dotenv; dev: pytest, httpx
  .env.example              YPT_URL=, PUBLIC_URL=https://<mac>.<tailnet>.ts.net, PORT=8787, CLAUDE_BIN=claude
  bridge/__init__.py
  bridge/config.py          env loading (python-dotenv, bridge/.env), paths, TZ, now()/today() helpers
  bridge/db.py              sqlite3 (stdlib), schema below, connect(), init_db(), seed() from seed/*.json
  bridge/materials.py       scan materials/<folder>/ -> {(kind, week): path}, filename rules below
  bridge/occurrences.py     materialize(date), next_unbriefed_week(), cancel/uncancel, pengganti
  bridge/ics.py             weekly calendar export
  bridge/app.py             FastAPI app: API routes + static mount of ../web at "/"
  bridge/ai.py              claude -p wrapper (only place that spawns claude)
  bridge/ypt.py             YPT MCP client over HTTP JSON-RPC
  bridge/jobs.py            evening job; `python -m bridge.jobs evening [--date YYYY-MM-DD]`
  seed/schedule.json        DONE: 8 courses, 12 weekly sessions
  seed/quotes.json          curated quotes [{text, author, source}]
  launchd/                  com.boredommaxxing.bridge.plist (KeepAlive), com.boredommaxxing.evening.plist (21:30)
  scripts/install-launchd.sh  copies plists to ~/Library/LaunchAgents (does NOT load; prints the launchctl commands)
  tests/                    pytest; never spawn real claude or hit real YPT in tests (monkeypatch)
  data/                     runtime, gitignored: app.sqlite3, photos/
web/                        static PWA, no build step, vanilla JS (ES modules ok)
  index.html, app.js, styles.css, sw.js, manifest.webmanifest, icons/
```

## DB schema (sqlite, `bridge/data/app.sqlite3`, `PRAGMA foreign_keys=ON`)

```sql
courses(code TEXT PRIMARY KEY, name TEXT NOT NULL, folder TEXT,           -- folder under materials/, nullable
        start_week INTEGER)                                                  -- skip material weeks < this (course already past them); NULL = no skip
sessions(id INTEGER PRIMARY KEY, course_code TEXT REFERENCES courses, kind TEXT CHECK(kind IN('K','R')),
         class_no INTEGER, weekday INTEGER, start TEXT, end TEXT, room TEXT)          -- weekly template
occurrences(id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions,           -- NULL for pengganti
            course_code TEXT NOT NULL REFERENCES courses, kind TEXT NOT NULL, date TEXT NOT NULL,  -- YYYY-MM-DD
            start TEXT NOT NULL, end TEXT NOT NULL, room TEXT,
            status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN('scheduled','cancelled')),
            is_pengganti INTEGER NOT NULL DEFAULT 0,
            week INTEGER, briefing_md TEXT, briefed_at TEXT,
            UNIQUE(session_id, date))
tasks(id INTEGER PRIMARY KEY, date TEXT NOT NULL, slot INTEGER CHECK(slot IN(1,2,3)),
      kind TEXT CHECK(kind IN('class','physical','wildcard')), text TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','passed','failed')),
      photo_path TEXT, verdict_reason TEXT, appealed INTEGER NOT NULL DEFAULT 0, appeal_note TEXT,
      UNIQUE(date, slot))
lock_log(id INTEGER PRIMARY KEY, ts TEXT NOT NULL, app TEXT NOT NULL)       -- ts ISO with +07:00
notes(date TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT)            -- "what did you learn?"
ypt_days(date TEXT PRIMARY KEY, total_minutes INTEGER, subjects_json TEXT, raw_json TEXT, fetched_at TEXT)
summaries(date TEXT PRIMARY KEY, text TEXT NOT NULL)                          -- Haiku join of ypt + note for that date
quotes(id INTEGER PRIMARY KEY, text TEXT NOT NULL, author TEXT NOT NULL, source TEXT, custom INTEGER NOT NULL DEFAULT 0,
       UNIQUE(text, author))
job_runs(id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, ok INTEGER, log TEXT)
```

`init_db()` is idempotent (CREATE IF NOT EXISTS) and seeds courses/sessions/quotes with INSERT OR IGNORE.

## Domain rules

- **Materials filenames** (keep originals, never rename): `Slide (Kuliah|Responsi) ?(\d+)` case-insensitive -> kind K/R, week. Note `Slide Responsi 11Solusi ...` has no space after the number. A filename containing `_revisi_` replaces the non-revisi file of the same (kind, week). Only `.pdf`. Course folder mapping from `courses.folder`.
- **Occurrences**: `materialize(date)` inserts one row per weekly session with `weekday == date.weekday()` (INSERT OR IGNORE). Called lazily by every read of a date and by the evening job.
- **Week assignment**: `next_unbriefed_week(course, kind)` = smallest material week >= courses.start_week (if set) for (course, kind) that is NOT already the `week` of any `scheduled` occurrence of that (course, kind). Assigned when the briefing is generated. Course without materials -> no week, no briefing (card only).
- **Cancel** (`POST /api/occurrences/{id}/cancel`): only allowed if now < occurrence start (else 409). Sets status=cancelled, week=NULL, briefing_md=NULL -> that week returns to the pool for the next occurrence / pengganti. Uncancel same time rule, status back to scheduled (briefing regenerated by job or `/brief`).
- **Pengganti** (`POST /api/occurrences` body `{course_code, kind, date, start, end, room?}`): inserts is_pengganti=1 row. If date is today or tomorrow, generate its briefing in a FastAPI BackgroundTask.
- **Tasks**: 3 per date (slot1 kind=class from that date's scheduled classes/materials — if no classes that day, a study task tied to any course; slot2 physical/real-world; slot3 wildcard). Generated by evening job for tomorrow. No reroll endpoint. Reveal: API returns `reveal_at` = `<date>T06:00:00+07:00`; API returns text always (PWA caches offline and hides until reveal_at — honour system).
- **Proof**: `POST /api/tasks/{id}/proof` multipart field `photo` (image/*, max 10 MB, verify magic bytes jpeg/png/heic/webp) -> saved `data/photos/<date>_<slot>_<uuid>.<ext>` -> if HEIC convert to JPEG copy with `sips -s format jpeg in --out out.jpg` -> ai.judge_photo(task_text, path) -> `{pass: bool, reason: str}` -> status passed/failed + verdict_reason. Re-submitting allowed while not passed.
- **Appeal**: `POST /api/tasks/{id}/appeal` `{note}` -> only if status=failed and appealed=0 -> status=passed, appealed=1, appeal_note=note. One per task.
- **Lock log**: `POST /lock-log` (`GET /lock-log` also works, same as `/api/lock-log`) body JSON `{"app": "Instagram"}` (also accept form or `?app=`; missing app -> "unknown"). Returns 204 fast, never fails on extra fields. Shortcut is fire-and-forget.
- **YPT**: `ypt.fetch_me_all()` posts JSON-RPC to `YPT_URL` (MCP streamable HTTP): `initialize` -> `notifications/initialized` -> `tools/call {name:"me_all", arguments:{}}`; send header `Accept: application/json, text/event-stream`, carry `Mcp-Session-Id` if returned; parse either JSON body or SSE `data:` lines. Result content is text JSON. Server returns YESTERDAY's data only (premium inactive). Store into ypt_days for that date with total minutes + per-subject minutes (be defensive: unknown shape -> store raw_json, total NULL). If YPT_URL unset -> skip with log line, never crash.
- **AI** (`ai.py`): `run_claude(prompt, *, allowed_tools=(), add_dirs=(), timeout=180) -> str` spawns `[CLAUDE_BIN, "-p", prompt, "--model", "claude-haiku-5-5", "--effort", "high", "--output-format", "json", "--disallowedTools", <ALL 9 pluang place/cancel tools + Bash Write Edit>, ("--allowedTools", ...) , ("--add-dir", ...)]`, parses `result` from JSON stdout. ALSO always pass `--safe-mode --strict-mcp-config --mcp-config '{"mcpServers":{}}' --no-session-persistence` (verified 2026-10-21: keeps Pro OAuth login, disables user hooks/plugins/CLAUDE.md/MCP that would otherwise corrupt output; `--bare` is NOT usable, it needs an API key). Run with `cwd=bridge/data/`. `CLAUDE_BIN` default = absolute path from `shutil.which('claude')` falling back to `~/.local/bin/claude` (launchd has minimal PATH). Raise `AIError` on non-zero exit, timeout, `is_error: true`, or unparseable output. Functions: `brief(course_name, kind, week, pdf_path) -> markdown` (Read tool on PDF; short: key concepts, 3 questions to think about, terms; Indonesian source, answer in English unless material demands), `make_tasks(date, classes_ctx) -> list[3 {kind,text}]`, `judge_photo(task_text, image_path) -> {pass, reason}` (Read tool on image; strict JSON; reason one line; obvious screen photos -> fail), `summarize_day(date, ypt_day, note) -> str`. Parse model JSON defensively (strip code fences, find first `{`/`[`). Pluang tools to disallow: `mcp__claude_ai_pluang__{us_stocks,crypto,indo_stocks}_{place_buy_order,place_sell_order,cancel_order}`.
- **Evening job** (21:30 launchd, target = tomorrow): materialize(tomorrow); brief each scheduled occurrence lacking briefing whose course has materials; make tasks if missing; fetch YPT (yesterday from server's view = today's date-1) -> ypt_days; summarize_day for that date if note or ypt exists. Each step isolated try/except, logged to job_runs; one failure never stops the rest. Idempotent (rerun does no duplicate work).
- **ICS** (`GET /calendar.ics`, `text/calendar`): one VEVENT per weekly session, `RRULE:FREQ=WEEKLY`, DTSTART on the next occurrence of that weekday with `TZID=Asia/Jakarta` + VTIMEZONE block, SUMMARY `"<course name> (K|R)"`, LOCATION room, `URL:<PUBLIC_URL>/#/class/<session_id>`, DESCRIPTION same URL, `VALARM TRIGGER:-PT30M ACTION:DISPLAY`. Stable UID `session-<id>@boredom-maxxing`. CRLF line endings, escape `,;\` per RFC 5545.

## HTTP API (JSON; dates `YYYY-MM-DD`; all under 127.0.0.1:PORT, exposed via `tailscale serve`)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/status | | `{ok, now, ypt_configured, public_url, last_job: {job, finished_at, ok}|null}` |
| GET | /api/day/{date} | | `{date, occurrences: [Occ], tasks: [Task], quote: Quote}` (materializes date) |
| GET | /api/week/{date} | | `{days: [{date, occurrences:[Occ]}]}` 7 days from date |
| GET | /api/occurrences/{id} | | Occ |
| POST | /api/occurrences | `{course_code, kind, date, start, end, room?}` | Occ (201) |
| POST | /api/occurrences/{id}/cancel | | Occ / 409 |
| POST | /api/occurrences/{id}/uncancel | | Occ / 409 |
| POST | /api/occurrences/{id}/brief | | 202, generates in background |
| GET | /api/courses | | `[{code, name, folder, start_week, material_weeks: {K:[..],R:[..]}, sessions:[...]}]` |
| PATCH | /api/courses/{code} | `{start_week}` | course |
| POST | /api/tasks/{id}/proof | multipart `photo` | Task |
| POST | /api/tasks/{id}/appeal | `{note}` | Task / 409 |
| GET | /api/progress | `?days=30` | `{ypt: [{date,total_minutes,subjects}], notes: [{date,text}], summaries: [{date,text}], heatmap: [{date, passed, total}]}` |
| PUT | /api/notes/{date} | `{text}` | `{date,text}` |
| GET | /api/quotes/today | | Quote (deterministic daily rotation: index = days since 2026-01-01 mod count) |
| GET | /api/quotes | | [Quote] |
| POST | /api/quotes | `{text, author, source?}` | Quote (custom=1) |
| DELETE | /api/quotes/{id} | | 204, only custom=1 else 403 |
| GET | /api/lock-log | `?limit=100` | `[{id, ts, app}]` newest first + `counts_7d` |
| POST | /lock-log | `{app}` | 204 |
| GET | /calendar.ics | | text/calendar |
| GET | /photos/{name} | | the image (path-traversal safe) |
| GET | / | | web/index.html and static files |

Occ = `{id, session_id, course_code, course_name, kind, date, start, end, room, status, is_pengganti, week, briefing_md, has_materials, can_cancel, can_uncancel}`.
Task = `{id, date, slot, kind, text, status, verdict_reason, appealed, appeal_note, photo_url, reveal_at}`.
Quote = `{id, text, author, source, custom}`.

## PWA (web/)

- Tabs (bottom bar): **Today**, **Progress**, **Mind**, **More**. Hash routing: `#/today`, `#/progress`, `#/mind`, `#/more`, `#/class/<session_id>` (opens today's occurrence of that session; if none today, the next one in the coming 7 days).
- Today: date header, next-class card first, class list (time, name, K/R, room, cancelled state, "Cancel class" button when can_cancel, expandable briefing markdown rendered safely — escape HTML, minimal md: headings, lists, bold, code), 3 task cards (hidden "Revealed at 06:00" until reveal_at), camera button `<input type="file" accept="image/*" capture="environment">`, verdict + one appeal button. Add pengganti form (course, K/R, date, start, end, room).
- Progress: YPT minutes last 14 days (simple bar list), today's "what did you learn?" textarea (PUT notes), summaries, streak heatmap (GitHub-style grid, 3 passes = full).
- Mind: today's quote large, list of quotes, add own quote.
- More: lock log (count last 7 days + list), "Download calendar (.ics)" link, bridge status, per-course start week setting (courses with materials), setup notes (Shortcut steps short).
- Service worker: cache-first app shell; network-first for `/api/*` with cache fallback so last fetched day/tomorrow work offline; on load prefetch `/api/day/<today>` and `/api/day/<tomorrow>`. Offline banner when API unreachable; mutations disabled offline with clear message.
- manifest: name "Boredom-Maxxing", short_name "BM", display standalone, theme color matching design, icons 192/512 PNG + apple-touch-icon 180 (generate simple PNGs with Python stdlib/zlib or SVG->PNG with sips).
- Design: clean, minimal, one screen one decision, lots of whitespace, rounded cards, one accent color, system font (-apple-system). Respect safe-area insets (`viewport-fit=cover`, `env(safe-area-inset-*)`), dark mode via prefers-color-scheme. Copy the *feel* of `reference/*.jpg`, not the content.
- No external CDNs (works offline). All user/AI text inserted via textContent or escaped.

## Ops

- launchd plists: absolute `uv` path (`/Users/nabils/.local/bin/uv`), `WorkingDirectory` = repo root, `EnvironmentVariables` PATH=`/Users/nabils/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin`, `StandardOutPath`/`StandardErrorPath` under `bridge/data/logs/`. Bridge: `uv run --project bridge uvicorn bridge.app:app --host 127.0.0.1 --port 8787`, KeepAlive + RunAtLoad. Evening: `uv run --project bridge python -m bridge.jobs evening`, StartCalendarInterval Hour 21 Minute 30.
- App Lock Shortcut (user builds by hand on iPhone, documented in `docs/shortcuts.md`): automation "App is opened: Instagram/TikTok/...", Run Immediately. Actions in this order: 1) Show Alert with a STATIC quote text typed into the Shortcut (no network before blocking), 2) Go to Home Screen, 3) Get Contents of URL `POST <PUBLIC_URL>/lock-log` JSON `{app: <Shortcut Input name>}`. Order matters: block must happen even if POST fails. Same doc covers 06:00 and 21:00 Time-of-Day automations and importing `/calendar.ics`.
