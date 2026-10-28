"""Journal HTTP routes. Never imports app.py (circular); opens its own connections."""
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, media, moments, push

router = APIRouter()
MEDIA_NAME = re.compile(r"[0-9a-f]{32}(_thumb|_poster)?\.(jpg|mp4|m4a)")
MEDIA_TYPES = {".jpg": "image/jpeg", ".mp4": "video/mp4", ".m4a": "audio/mp4"}
SOON = "Your Moment is not open yet."


@contextmanager
def conn_():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


def url(name: Optional[str]) -> Optional[str]:
    return f"/journal-media/{name}" if name else None


def media_json(m) -> dict:
    kind = m["kind"]
    return {
        "id": m["id"], "entry_id": m["entry_id"], "position": m["position"], "kind": kind,
        "status": m["status"], "error": m["error"],
        "url": url(m["file"]), "thumb_url": url(m["thumb"]) if kind != "audio" else None,
        "poster_url": url(m["poster"]) if kind == "video" else None,
        "width": m["width"] if kind == "video" else None, "height": m["height"] if kind == "video" else None,
        "duration_ms": m["duration_ms"] if kind != "photo" else None, "bytes": m["bytes"],
        "transcript": None if kind == "photo" else {
            "status": m["transcript_status"], "text": m["transcript_text"],
            "language": m["transcript_lang"], "error": m["transcript_error"]},
        "created_at": m["created_at"],
    }


def entry_json(c, e) -> dict:
    moment = None
    if e["kind"] == "moment":
        d = c.execute("SELECT submitted_at, on_time FROM moment_days WHERE date=?", (e["moment_date"],)).fetchone()
        if d and d["submitted_at"]:
            moment = {"date": e["moment_date"], "submitted_at": d["submitted_at"], "on_time": bool(d["on_time"])}
    ms = c.execute("SELECT * FROM journal_media WHERE entry_id=? ORDER BY position, id", (e["id"],)).fetchall()
    return {"id": e["id"], "kind": e["kind"], "day": e["day"], "created_at": e["created_at"], "updated_at": e["updated_at"],
            "text": e["text"], "moment": moment, "media": [media_json(m) for m in ms]}


def load_entry(c, entry_id: int) -> dict:
    return entry_json(c, need_entry(c, entry_id))


def need_entry(c, entry_id: int):
    e = c.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if e is None:
        raise HTTPException(404, "entry not found")
    return e


def need_media(c, media_id: int):
    m = c.execute("SELECT * FROM journal_media WHERE id=?", (media_id,)).fetchone()
    if m is None:
        raise HTTPException(404, "media not found")
    return m


def moment_state_json(c, now) -> dict:
    return moments.moment_state(c, now, load_entry)


def file_paths(rows) -> list:
    paths = []
    for m in rows:
        paths += [m["file"] and config.JOURNAL_DIR / m["file"], m["thumb"] and config.JOURNAL_DIR / m["thumb"],
                  m["poster"] and config.JOURNAL_DIR / m["poster"], m["src_path"]]
    return paths


# ---- entries ----

class EntryIn(BaseModel):
    text: str = Field("", max_length=20000)


class EntryPatch(BaseModel):
    text: str = Field(max_length=20000)


@router.get("/api/journal")
def list_entries(limit: int = Query(30, ge=1, le=100), before_id: Optional[int] = Query(None, ge=1),
                 kind: Optional[Literal["free", "moment"]] = None):
    sql, args = "SELECT * FROM journal_entries WHERE 1=1", []
    if before_id is not None:
        sql += " AND id<?"
        args.append(before_id)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    with conn_() as c:
        rows = c.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1)).fetchall()
        page = rows[:limit]
        return {"entries": [entry_json(c, e) for e in page], "next_before_id": page[-1]["id"] if len(rows) > limit else None}


@router.post("/api/journal/entries", status_code=201)
def create_entry(body: EntryIn):
    now = config.now()
    ts = moments.iso(now)
    with conn_() as c:
        cur = c.execute("INSERT INTO journal_entries(kind, day, text, created_at, updated_at) VALUES('free',?,?,?,?)",
                        (moments.journal_day(now), body.text, ts, ts))
        c.commit()
        return load_entry(c, cur.lastrowid)


@router.get("/api/journal/entries/{entry_id}")
def get_entry(entry_id: int):
    with conn_() as c:
        return load_entry(c, entry_id)


@router.patch("/api/journal/entries/{entry_id}")
def patch_entry(entry_id: int, body: EntryPatch):
    with conn_() as c:
        need_entry(c, entry_id)
        c.execute("UPDATE journal_entries SET text=?, updated_at=? WHERE id=?", (body.text, moments.iso(config.now()), entry_id))
        c.commit()
        return load_entry(c, entry_id)


@router.delete("/api/journal/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: int):
    with conn_() as c:
        e = need_entry(c, entry_id)
        paths = file_paths(c.execute("SELECT * FROM journal_media WHERE entry_id=?", (entry_id,)))
        if e["kind"] == "moment":  # un-count the day
            c.execute("UPDATE moment_days SET entry_id=NULL, submitted_at=NULL, on_time=NULL WHERE entry_id=?", (entry_id,))
        c.execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))
        c.commit()
    media.unlink(*paths)
    return Response(status_code=204)


# ---- media ----

@router.post("/api/journal/entries/{entry_id}/media", status_code=201)
def add_media(entry_id: int, file: UploadFile = File(...)):
    with conn_() as c:
        e = need_entry(c, entry_id)
        if e["kind"] == "moment":
            raise HTTPException(409, "A Moment holds one photo or video. Replace the Moment instead.")
        n = c.execute("SELECT COUNT(*) AS n FROM journal_media WHERE entry_id=?", (entry_id,)).fetchone()["n"]
        if n >= media.MAX_MEDIA_PER_ENTRY:
            raise HTTPException(409, "An entry holds at most 10 attachments.")
    item = media.ingest(file.file)
    photo = media.process_photo(item) if item.kind == "photo" else None
    try:
        with conn_() as c:
            pos = c.execute("SELECT COALESCE(MAX(position)+1, 0) AS p FROM journal_media WHERE entry_id=?", (entry_id,)).fetchone()["p"]
            mid = media.insert_media(c, entry_id, pos, item, photo)
            c.commit()
            return media_json(need_media(c, mid))
    except BaseException:  # e.g. the entry was deleted meanwhile (FK error)
        media.unlink(item.src)
        if photo:
            media.unlink(config.JOURNAL_DIR / photo["file"], config.JOURNAL_DIR / photo["thumb"])
        raise


@router.get("/api/journal/media/{media_id}")
def get_media(media_id: int):
    with conn_() as c:
        return media_json(need_media(c, media_id))


@router.delete("/api/journal/media/{media_id}", status_code=204)
def delete_media(media_id: int):
    with conn_() as c:
        m = need_media(c, media_id)
        if c.execute("SELECT kind FROM journal_entries WHERE id=?", (m["entry_id"],)).fetchone()["kind"] == "moment":
            raise HTTPException(409, "Replace the Moment instead of removing its media.")
        c.execute("DELETE FROM journal_media WHERE id=?", (media_id,))
        c.commit()
    media.unlink(*file_paths([m]))
    return Response(status_code=204)


@router.post("/api/journal/media/{media_id}/retranscribe", status_code=202)
def retranscribe(media_id: int):
    with conn_() as c:
        m = need_media(c, media_id)
        if m["kind"] == "photo" or m["transcript_status"] not in ("done", "error"):
            raise HTTPException(409, "This item cannot be transcribed again right now.")
        c.execute("UPDATE journal_media SET transcript_status='pending', transcript_text=NULL, transcript_lang=NULL, "
                  "transcript_error=NULL, updated_at=? WHERE id=?", (moments.iso(config.now()), media_id))
        c.commit()
        return media_json(need_media(c, media_id))


# ---- Moment ----

@router.get("/api/journal/moment")
def get_moment():
    with conn_() as c:
        return moment_state_json(c, config.now())


@router.post("/api/journal/moment", status_code=201)
def post_moment(file: UploadFile = File(...), caption: Optional[str] = Form(None)):
    if caption is not None and len(caption) > 20000:
        raise HTTPException(422, "Caption is too long.")
    now = config.now()
    with conn_() as c:
        day = moments.journal_day(now)
        if now < moments.ping_at(moments.ensure_day(c, day, now)):
            raise HTTPException(409, SOON)
        item = media.ingest(file.file, allow_audio=False)
        photo = media.process_photo(item) if item.kind == "photo" else None
        try:
            old = moments.submit(c, day, now, item, photo, caption)
        except BaseException:
            media.unlink(item.src)
            if photo:
                media.unlink(config.JOURNAL_DIR / photo["file"], config.JOURNAL_DIR / photo["thumb"])
            raise
        media.unlink(*old)
        return moment_state_json(c, now)


@router.get("/api/journal/moments")
def get_moments(days: int = Query(30, ge=1, le=400)):
    with conn_() as c:
        return {"days": moments.history(c, days, config.now())}


# ---- settings ----

class SettingsIn(BaseModel):
    window_start: str
    window_end: str


@router.get("/api/journal/settings")
def get_settings():
    with conn_() as c:
        return moments.settings_json(c, config.now())


@router.put("/api/journal/settings")
def put_settings(body: SettingsIn):
    with conn_() as c:
        try:
            moments.put_settings(c, body.window_start, body.window_end)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return moments.settings_json(c, config.now())


# ---- push ----

class PushKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=200)


class SubscribeIn(BaseModel):
    endpoint: str = Field(max_length=1024)
    expirationTime: Optional[float] = None
    keys: PushKeys


class UnsubscribeIn(BaseModel):
    endpoint: str = Field(max_length=1024)


@router.get("/api/push/public-key")
def public_key():
    return {"public_key": push.public_key_b64()}


@router.post("/api/push/subscribe", status_code=201)
def subscribe(body: SubscribeIn, request: Request):
    if not push.allowed_endpoint(body.endpoint):
        raise HTTPException(422, "Unsupported push service.")
    with conn_() as c:
        sid = push.subscribe(c, body.endpoint, body.keys.p256dh, body.keys.auth, request.headers.get("user-agent"))
    return {"id": sid}


@router.post("/api/push/unsubscribe", status_code=204)
def unsubscribe(body: UnsubscribeIn):
    with conn_() as c:
        push.unsubscribe(c, body.endpoint)
    return Response(status_code=204)


@router.post("/api/push/test")
def push_test():
    with conn_() as c:
        r = push.send_all(c, push.self_test_payload())
    return {"sent": r.sent, "failed": r.failed + r.transient, "removed": r.removed}


# ---- files ----

@router.api_route("/journal-media/{name}", methods=["GET", "HEAD"])
def journal_media(name: str):
    if not MEDIA_NAME.fullmatch(name):
        raise HTTPException(404)
    p = config.JOURNAL_DIR / name
    if not p.is_file() or p.resolve().parent != config.JOURNAL_DIR.resolve():
        raise HTTPException(404)
    # Names are never reused, so immutable caching is safe. Starlette's FileResponse answers Range with 206.
    return FileResponse(p, media_type=MEDIA_TYPES[Path(name).suffix],
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})
