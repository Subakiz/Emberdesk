import asyncio
import json
import logging
import re
import subprocess
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Optional
from urllib.parse import parse_qs

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints

from . import ai, config, db, ics, jobs, journal, materials, media, moments, occurrences, push, screentime

log = logging.getLogger("bridge.app")

MAX_PHOTO = 10 * 1024 * 1024
HEIC_BRANDS = {b"heic", b"heix", b"mif1", b"msf1", b"hevc"}
PHOTO_NAME = re.compile(r"[\w.-]+", re.ASCII)
JOURNAL_UPLOAD = re.compile(r"/api/journal/entries/\d+/media|/api/journal/moment")
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
QUOTE_EPOCH = date_cls(2026, 1, 1)


@asynccontextmanager
async def lifespan(_app):
    with conn_() as c:
        db.init_db(c)
    async def screen_time_loop():
        while True:
            await asyncio.sleep(30 * 60)
            try:
                await asyncio.to_thread(screentime.scheduled_refresh)
            except Exception:
                log.exception("scheduled Screen Time import failed")

    push.ensure_keys()
    loops = []
    if config.BACKGROUND:
        media.recover()  # before any request can write into JOURNAL_TMP
        loops = [asyncio.create_task(media.worker_loop()), asyncio.create_task(moments.scheduler_loop()),
                 asyncio.create_task(screen_time_loop())]
    try:
        yield
    finally:
        for t in loops:
            t.cancel()
        await asyncio.gather(*loops, return_exceptions=True)


app = FastAPI(title="Boredom-Maxxing bridge", lifespan=lifespan)


# One connection per request, opened in the thread that uses it (sqlite3 forbids sharing across threads).
@contextmanager
def conn_():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def parse_date(s: str) -> str:
    try:
        return date_cls.fromisoformat(s).isoformat()
    except ValueError:
        raise HTTPException(422, f"bad date {s!r}, want YYYY-MM-DD")


def task_json(r) -> dict:
    return {
        "id": r["id"], "date": r["date"], "slot": r["slot"], "kind": r["kind"], "text": r["text"],
        "status": r["status"], "verdict_reason": r["verdict_reason"],
        "appealed": bool(r["appealed"]), "appeal_note": r["appeal_note"],
        "photo_url": f"/photos/{Path(r['photo_path']).name}" if r["photo_path"] else None,
        "reveal_at": f"{r['date']}T06:00:00+07:00",
    }


def quote_json(r) -> dict:
    return {"id": r["id"], "text": r["text"], "author": r["author"], "source": r["source"], "custom": bool(r["custom"])}


def quote_for(conn, date: str) -> Optional[dict]:
    rows = conn.execute("SELECT * FROM quotes ORDER BY id").fetchall()
    if not rows:
        return None
    return quote_json(rows[(date_cls.fromisoformat(date) - QUOTE_EPOCH).days % len(rows)])


def get_task(conn, task_id: int):
    r = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "task not found")
    return r


def need_occ(conn, occ_id: int) -> dict:
    occ = occurrences.get(conn, occ_id)
    if occ is None:
        raise HTTPException(404, "occurrence not found")
    return occ


def brief_bg(occ_id: int) -> None:
    try:
        with conn_() as c:
            jobs.brief_occurrence(c, occ_id)
    except Exception:
        log.exception("background briefing failed for occurrence %s", occ_id)


# ---- status / day / week ----

@app.get("/api/status")
def status():
    with conn_() as c:
        j = c.execute("SELECT job, finished_at, ok FROM job_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        subs = c.execute("SELECT COUNT(*) AS n FROM push_subscriptions").fetchone()["n"]
    s = config.settings()
    is_file = lambda p: bool(p) and Path(p).is_file()
    return {
        "ok": True, "now": config.now().isoformat(), "ypt_configured": bool(s.YPT_URL), "public_url": s.PUBLIC_URL,
        "last_job": {"job": j["job"], "finished_at": j["finished_at"], "ok": bool(j["ok"])} if j else None,
        "journal": {"ffmpeg": is_file(s.FFMPEG_BIN) and is_file(s.FFPROBE_BIN),
                    "whisper": is_file(s.WHISPER_BIN) and is_file(s.WHISPER_MODEL), "push_subscriptions": subs},
    }


@app.get("/api/day/{date}")
def day(date: str):
    date = parse_date(date)
    with conn_() as c:
        occs = occurrences.list_day(c, date)
        tasks = [task_json(r) for r in c.execute("SELECT * FROM tasks WHERE date=? ORDER BY slot", (date,))]
        return {"date": date, "occurrences": occs, "tasks": tasks, "quote": quote_for(c, date)}


@app.get("/api/week/{date}")
def week(date: str):
    start = date_cls.fromisoformat(parse_date(date))
    with conn_() as c:
        days = []
        for i in range(7):
            d = (start + timedelta(days=i)).isoformat()
            days.append({"date": d, "occurrences": occurrences.list_day(c, d)})
    return {"days": days}


# ---- occurrences ----

class PenggantiIn(BaseModel):
    course_code: str
    kind: Literal["K", "R"]
    date: str
    start: str
    end: str
    room: Optional[str] = None


@app.get("/api/occurrences/{occ_id}")
def get_occurrence(occ_id: int):
    with conn_() as c:
        return need_occ(c, occ_id)


@app.post("/api/occurrences", status_code=201)
def create_occurrence(body: PenggantiIn, bg: BackgroundTasks):
    date = parse_date(body.date)
    if not (TIME_RE.fullmatch(body.start) and TIME_RE.fullmatch(body.end)):
        raise HTTPException(422, "start/end must be HH:MM")
    if body.end <= body.start:
        raise HTTPException(422, "end must be after start")
    with conn_() as c:
        if c.execute("SELECT 1 FROM courses WHERE code=?", (body.course_code,)).fetchone() is None:
            raise HTTPException(404, "course not found")
        try:
            occ = occurrences.create_pengganti(c, body.course_code, body.kind, date, body.start, body.end, body.room or None)
        except occurrences.Conflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
    today = config.now().date()
    if date in (today.isoformat(), (today + timedelta(days=1)).isoformat()):
        bg.add_task(brief_bg, occ["id"])
    return occ


@app.post("/api/occurrences/{occ_id}/cancel")
def cancel_occurrence(occ_id: int):
    with conn_() as c:
        need_occ(c, occ_id)
        try:
            return occurrences.cancel(c, occ_id)
        except occurrences.Conflict as e:
            raise HTTPException(409, str(e))


@app.post("/api/occurrences/{occ_id}/uncancel")
def uncancel_occurrence(occ_id: int):
    with conn_() as c:
        need_occ(c, occ_id)
        try:
            return occurrences.uncancel(c, occ_id)
        except occurrences.Conflict as e:
            raise HTTPException(409, str(e))


@app.post("/api/occurrences/{occ_id}/brief", status_code=202)
def brief_occurrence(occ_id: int, bg: BackgroundTasks):
    with conn_() as c:
        need_occ(c, occ_id)
    bg.add_task(brief_bg, occ_id)
    return {"status": "queued", "id": occ_id}


# ---- courses ----

class CoursePatch(BaseModel):
    start_week: Optional[int] = Field(None, ge=1)


def course_json(c, r) -> dict:
    sessions = [dict(s) for s in c.execute("SELECT * FROM sessions WHERE course_code=? ORDER BY weekday, start", (r["code"],))]
    return {
        "code": r["code"], "name": r["name"], "folder": r["folder"], "start_week": r["start_week"],
        "material_weeks": materials.weeks(c, r["code"]), "sessions": sessions,
    }


@app.get("/api/courses")
def courses():
    with conn_() as c:
        return [course_json(c, r) for r in c.execute("SELECT * FROM courses ORDER BY code")]


@app.patch("/api/courses/{code}")
def patch_course(code: str, body: CoursePatch):
    with conn_() as c:
        if c.execute("SELECT 1 FROM courses WHERE code=?", (code,)).fetchone() is None:
            raise HTTPException(404, "course not found")
        c.execute("UPDATE courses SET start_week=? WHERE code=?", (body.start_week, code))
        c.commit()
        return course_json(c, c.execute("SELECT * FROM courses WHERE code=?", (code,)).fetchone())


# ---- tasks ----

def sniff_ext(b: bytes) -> Optional[str]:
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:4] == b"\x89PNG":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[4:8] == b"ftyp" and b[8:12] in HEIC_BRANDS:
        return "heic"
    return None


def read_limited(f) -> bytes:
    buf = bytearray()
    while chunk := f.read(65536):
        buf += chunk
        if len(buf) > MAX_PHOTO:
            raise HTTPException(413, "photo larger than 10 MB")
    return bytes(buf)


# Starlette spools the whole multipart body to disk before the handler runs, so reject by header first.
# Proof uploads without Content-Length still reach read_limited (tailnet-only, single user).
@app.middleware("http")
async def limit_proof_upload(request: Request, call_next):
    # CSRF: a cross-site page can send multipart/form-data without a preflight. Absent header (curl, Shortcuts) passes.
    if request.method not in ("GET", "HEAD") and request.headers.get("sec-fetch-site") == "cross-site":
        return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
    if request.method == "POST" and request.url.path.endswith("/proof"):
        try:
            too_big = int(request.headers.get("content-length", 0)) > MAX_PHOTO + 65536
        except ValueError:
            too_big = False
        if too_big:
            return JSONResponse({"detail": "photo larger than 10 MB"}, status_code=413)
    # Same header pre-check for Journal uploads; media.MAX_UPLOAD is read at call time so tests can patch it.
    # Chunked bodies are refused: Starlette would spool the whole file to the system temp dir before ingest can count it.
    if request.method == "POST" and JOURNAL_UPLOAD.fullmatch(request.url.path):
        if "content-length" not in request.headers:
            return JSONResponse({"detail": "Length required."}, status_code=411)
        try:
            too_big = int(request.headers["content-length"]) > media.MAX_UPLOAD + media.MiB
        except ValueError:
            too_big = False
        if too_big:
            return JSONResponse({"detail": "File larger than 150 MB."}, status_code=413)
    return await call_next(request)


@app.post("/api/tasks/{task_id}/proof")
def proof(task_id: int, photo: UploadFile = File(...)):
    data = read_limited(photo.file)
    ext = sniff_ext(data)
    if ext is None:
        raise HTTPException(415, "not a jpeg/png/webp/heic image")
    with conn_() as c:
        t = get_task(c, task_id)
        if t["status"] == "passed":
            raise HTTPException(409, "task already passed")
        config.PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        path = config.PHOTOS_DIR / f"{t['date']}_{t['slot']}_{uuid.uuid4().hex}.{ext}"
        path.write_bytes(data)
        if ext == "heic":
            jpg = path.with_suffix(".jpg")
            try:
                r = subprocess.run(["sips", "-s", "format", "jpeg", str(path), "--out", str(jpg)],
                                   capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                r = None
            if r is None or r.returncode != 0 or not jpg.exists():
                raise HTTPException(422, "could not convert HEIC photo")
            path = jpg
        # Store before judging so the photo is kept if the judge fails.
        c.execute("UPDATE tasks SET photo_path=? WHERE id=? AND status!='passed'", (str(path), task_id))
        c.commit()
        try:
            verdict = ai.judge_photo(t["text"], path)
        except ai.AIError as e:
            raise HTTPException(502, f"judge failed: {e}")
        # A concurrent upload may have passed meanwhile; never overwrite a pass.
        c.execute("UPDATE tasks SET status=?, verdict_reason=?, photo_path=? WHERE id=? AND status!='passed'",
                  ("passed" if verdict.get("pass") else "failed", str(verdict.get("reason") or "")[:500],
                   str(path), task_id))
        c.commit()
        return task_json(get_task(c, task_id))


class AppealIn(BaseModel):
    note: str = Field("", max_length=2000)


@app.post("/api/tasks/{task_id}/appeal")
def appeal(task_id: int, body: AppealIn):
    with conn_() as c:
        t = get_task(c, task_id)
        if t["status"] != "failed" or t["appealed"]:
            raise HTTPException(409, "only a failed, not-yet-appealed task can be appealed")
        c.execute("UPDATE tasks SET status='passed', appealed=1, appeal_note=? WHERE id=?", (body.note, task_id))
        c.commit()
        return task_json(get_task(c, task_id))


# ---- progress / notes ----

class NoteIn(BaseModel):
    text: str = Field(max_length=20000)


@app.get("/api/progress")
def progress(days: int = 30):
    days = max(1, min(days, 400))
    cutoff = (config.now().date() - timedelta(days=days)).isoformat()
    with conn_() as c:
        ypt = []
        for r in c.execute("SELECT * FROM ypt_days WHERE date>=? ORDER BY date", (cutoff,)):
            try:
                subjects = json.loads(r["subjects_json"]) if r["subjects_json"] else None
            except ValueError:
                subjects = None
            ypt.append({"date": r["date"], "total_minutes": r["total_minutes"], "subjects": subjects})
        notes = [dict(r) for r in c.execute("SELECT date, text FROM notes WHERE date>=? ORDER BY date", (cutoff,))]
        summaries = [dict(r) for r in c.execute("SELECT date, text FROM summaries WHERE date>=? ORDER BY date", (cutoff,))]
        heatmap = [dict(r) for r in c.execute(
            "SELECT date, SUM(status='passed') AS passed, COUNT(*) AS total FROM tasks WHERE date>=? GROUP BY date ORDER BY date",
            (cutoff,))]
    return {"ypt": ypt, "notes": notes, "summaries": summaries, "heatmap": heatmap}


@app.put("/api/notes/{date}")
def put_note(date: str, body: NoteIn):
    date = parse_date(date)
    with conn_() as c:
        c.execute(
            "INSERT INTO notes(date, text, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(date) DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at",
            (date, body.text, config.now().isoformat()))
        c.commit()
    return {"date": date, "text": body.text}


# ---- quotes ----

class QuoteIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    author: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    source: Optional[str] = Field(None, max_length=300)


@app.get("/api/quotes/today")
def quote_today():
    with conn_() as c:
        q = quote_for(c, config.today())
    if q is None:
        raise HTTPException(404, "no quotes")
    return q


@app.get("/api/quotes")
def quotes():
    with conn_() as c:
        return [quote_json(r) for r in c.execute("SELECT * FROM quotes ORDER BY id")]


@app.post("/api/quotes")
def add_quote(body: QuoteIn):
    with conn_() as c:
        try:
            cur = c.execute("INSERT INTO quotes(text, author, source, custom) VALUES(?,?,?,1)",
                            (body.text, body.author, body.source or None))
        except Exception as e:  # sqlite3.IntegrityError on UNIQUE(text, author)
            if "UNIQUE" in str(e):
                raise HTTPException(409, "quote already exists")
            raise
        c.commit()
        return quote_json(c.execute("SELECT * FROM quotes WHERE id=?", (cur.lastrowid,)).fetchone())


@app.delete("/api/quotes/{quote_id}", status_code=204)
def delete_quote(quote_id: int):
    with conn_() as c:
        r = c.execute("SELECT custom FROM quotes WHERE id=?", (quote_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "quote not found")
        if not r["custom"]:
            raise HTTPException(403, "only custom quotes can be deleted")
        c.execute("DELETE FROM quotes WHERE id=?", (quote_id,))
        c.commit()
    return Response(status_code=204)


# ---- lock log ----

@app.get("/api/lock-log")
@app.get("/lock-log")  # Shortcut URL opened in Safari does GET
def lock_log(limit: int = 100):
    limit = max(1, min(limit, 1000))
    cutoff = (config.now() - timedelta(days=7)).isoformat()
    with conn_() as c:
        items = [dict(r) for r in c.execute("SELECT id, ts, app FROM lock_log ORDER BY id DESC LIMIT ?", (limit,))]
        counts = {r["app"]: r["n"] for r in c.execute(
            "SELECT app, COUNT(*) AS n FROM lock_log WHERE ts>=? GROUP BY app ORDER BY n DESC", (cutoff,))}
    return {"items": items, "counts_7d": counts}


# The Shortcut is fire-and-forget, so any junk body must still log and return 204.
@app.post("/lock-log", status_code=204)
async def post_lock_log(request: Request):
    name = None
    try:
        name = request.query_params.get("app")
        if not name:
            raw = await request.body()
            try:
                j = json.loads(raw)
                name = j.get("app") if isinstance(j, dict) else None
            except ValueError:
                name = parse_qs(raw.decode("utf-8", "replace")).get("app", [None])[0]
                if not name:
                    name = (await request.form()).get("app")
    except Exception:  # incl. RecursionError on absurdly nested JSON
        name = None
    name = (name.strip() if isinstance(name, str) else "")[:64] or "unknown"
    with conn_() as c:
        c.execute("INSERT INTO lock_log(ts, app) VALUES(?,?)", (config.now().isoformat(), name))
        c.commit()
    return Response(status_code=204)


# ---- screen time ----

def screen_time_days(days: int) -> int:
    if days not in (1, 7, 30):
        raise HTTPException(422, "days must be 1, 7, or 30")
    return days


class ScreenTimeTotal(BaseModel):
    tiktok_minutes: int = Field(ge=0, le=1440, strict=True)
    instagram_minutes: int = Field(ge=0, le=1440, strict=True)


@app.get("/api/screen-time")
def screen_time(days: int = 7):
    with conn_() as c:
        return screentime.summary(c, screen_time_days(days))


@app.put("/api/screen-time/{date}")
def save_screen_time(date: str, body: ScreenTimeTotal):
    date = parse_date(date)
    if date > config.today():
        raise HTTPException(422, "Screen Time cannot be logged for a future date")
    with conn_() as c:
        c.execute("INSERT INTO screen_time_days VALUES(?,'manual',?,?,?) "
                  "ON CONFLICT(date,source) DO UPDATE SET tiktok_seconds=excluded.tiktok_seconds, "
                  "instagram_seconds=excluded.instagram_seconds,updated_at=excluded.updated_at",
                  (date, body.tiktok_minutes * 60, body.instagram_minutes * 60, config.now().isoformat()))
        c.commit()
    return {"date": date}


@app.get("/api/screen-time/{date}")
def screen_time_date(date: str):
    date = parse_date(date)
    with conn_() as c:
        row = c.execute("SELECT * FROM screen_time_days WHERE date=? ORDER BY source DESC LIMIT 1", (date,)).fetchone()
    return dict(row) if row else {"date": date, "source": None, "tiktok_seconds": None, "instagram_seconds": None}


@app.delete("/api/screen-time/{date}", status_code=204)
def reset_screen_time(date: str):
    date = parse_date(date)
    with conn_() as c:
        c.execute("DELETE FROM screen_time_days WHERE date=? AND source='manual'", (date,))
        c.commit()
    return Response(status_code=204)


@app.post("/api/screen-time/sync")
def sync_screen_time():
    with conn_() as c:
        status = screentime.refresh(c)
    return status


@app.post("/api/screen-time/ideas")
def screen_time_ideas(days: int = 7):
    with conn_() as c:
        try:
            return screentime.generate(c, screen_time_days(days))
        except ValueError as e:
            raise HTTPException(422, str(e))
        except ai.AIError:
            log.exception("Screen Time idea generation failed")
            raise HTTPException(503, "Ideas could not be generated. Check Claude on your Mac, then try again.")


# ---- calendar / photos / static ----

@app.api_route("/calendar.ics", methods=["GET", "HEAD"])  # iOS probes with HEAD
def calendar():
    with conn_() as c:
        body = ics.build_ics(c, config.settings().PUBLIC_URL)
    return Response(body, media_type="text/calendar; charset=utf-8")


@app.get("/photos/{name}")
def photo(name: str):
    if not PHOTO_NAME.fullmatch(name):
        raise HTTPException(404)
    p = config.PHOTOS_DIR / name
    if not p.is_file() or p.resolve().parent != config.PHOTOS_DIR.resolve():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def root_icons():  # iOS/Safari request these at the root regardless of <link>
    return FileResponse(config.WEB_DIR / "icons" / "apple-touch-icon.png")


@app.get("/sw.js")
def service_worker():
    return FileResponse(config.WEB_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


app.include_router(journal.router)


# Must come last so API routes win.
if config.WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=config.WEB_DIR, html=True), name="web")
