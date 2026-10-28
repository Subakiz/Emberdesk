"""Journal media: upload intake, photo pipeline, ffmpeg/whisper worker.

Every subprocess goes through `_run` so tests can replace it. No DB write transaction is held while one runs.
"""
import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from . import config, db

log = logging.getLogger("bridge.media")

MiB = 1024 * 1024
MAX_UPLOAD = 150 * MiB
MAX_PHOTO = 30 * MiB
MAX_AUDIO = 50 * MiB
MAX_VIDEO = 150 * MiB
MAX_VIDEO_SECONDS = 60
MAX_AUDIO_SECONDS = 600
MAX_MEDIA_PER_ENTRY = 10

SIPS = "/usr/bin/sips"
HEIC_BRANDS = {b"heic", b"heix", b"mif1", b"msf1", b"hevc"}
# Dropped from transcripts: whisper's non-speech markers such as [BLANK_AUDIO] or (music).
NOISE_SEGMENT = re.compile(r"^[\[(][^\])]*[\])]$")


class MediaError(Exception):
    pass


class BinaryMissing(MediaError):
    pass


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run without a shell; raise MediaError on missing binary, timeout or non-zero exit."""
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise BinaryMissing(f"{Path(args[0]).name} is not installed")
    except subprocess.TimeoutExpired:
        raise MediaError(f"{Path(args[0]).name} timed out")
    if r.returncode != 0:
        tail = r.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        tail[0] = tail[0].replace(str(config.DATA_DIR), "…")  # keep absolute data paths out of API errors
        raise MediaError(f"{Path(args[0]).name} failed ({r.returncode}): {tail[0][:200]}")
    return r


def now_iso() -> str:
    return config.now().isoformat(timespec="seconds")


def sniff(head: bytes) -> Optional[str]:
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:4] == b"\x89PNG":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp":
        return "heic" if head[8:12] in HEIC_BRANDS else "isobmff"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if head[:4] == b"OggS":
        return "ogg"
    return None


def unlink(*paths) -> None:
    for p in paths:
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                log.warning("could not remove %s", p)


def cleanup_uid(uid: str) -> None:
    for p in config.JOURNAL_TMP.glob(f"{uid}*"):
        unlink(p)


def probe(path: Path) -> dict:
    """ffprobe summary: duration seconds (or None), width/height of the first real video stream, has_audio."""
    ff = config.settings().FFPROBE_BIN
    out = _run([ff, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], 15).stdout
    try:
        data = json.loads(out)
        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"
                      and (s.get("disposition") or {}).get("attached_pic", 0) != 1), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        try:
            dur = float((data.get("format") or {}).get("duration"))
            dur = dur if math.isfinite(dur) else None
        except (TypeError, ValueError):
            dur = None
        return {"duration": dur, "has_video": video is not None, "has_audio": audio is not None,
                "width": video.get("width") if video else None, "height": video.get("height") if video else None}
    except (ValueError, AttributeError):
        raise MediaError("could not read ffprobe output")


# ---- intake ----

@dataclass
class Item:
    uid: str
    src: Path
    kind: str  # photo | video | audio
    size: int
    duration: Optional[float] = None


def ingest(f, *, allow_audio: bool = True) -> Item:
    """Steps 3-5 of the upload protocol: stream to tmp, sniff, classify. Leaves the upload in JOURNAL_TMP on success."""
    config.JOURNAL_TMP.mkdir(parents=True, exist_ok=True)
    uid = uuid.uuid4().hex
    src = config.JOURNAL_TMP / f"{uid}.upload"
    try:
        size = 0
        with open(src, "wb") as out:
            while chunk := f.read(MiB):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "File larger than 150 MB.")
                out.write(chunk)
        with open(src, "rb") as fh:
            kind = sniff(fh.read(16))
        if kind is None:
            raise HTTPException(415, "Not a supported photo, video or audio file.")
        if kind in ("jpg", "png", "webp", "heic"):
            if size > MAX_PHOTO:
                raise HTTPException(413, "Photos can be at most 30 MB.")
            return Item(uid, src, "photo", size)
        try:
            info = probe(src)
        except BinaryMissing:
            raise HTTPException(503, "ffmpeg is not installed on the Mac.")
        except MediaError:
            raise HTTPException(422, "Could not read this file.")
        dur = info["duration"]
        if info["has_video"]:
            if size > MAX_VIDEO:
                raise HTTPException(413, "Videos can be at most 150 MB.")
            if dur is not None and dur > MAX_VIDEO_SECONDS + 1:
                raise HTTPException(422, "Videos can be at most 60 seconds.")
            return Item(uid, src, "video", size, dur)
        if info["has_audio"]:
            if not allow_audio:
                raise HTTPException(415, "A Moment is one photo or one video.")
            if size > MAX_AUDIO:
                raise HTTPException(413, "Voice notes can be at most 50 MB.")
            if dur is not None and dur > MAX_AUDIO_SECONDS + 1:
                raise HTTPException(422, "Voice notes can be at most 10 minutes.")
            return Item(uid, src, "audio", size, dur)
        raise HTTPException(422, "No playable audio or video found.")
    except BaseException:
        cleanup_uid(uid)
        raise


def _sips_int(out: str, key: str) -> int:
    m = re.search(rf"{key}:\s*(\d+)", out)
    if not m:
        raise MediaError(f"sips gave no {key}")
    return int(m.group(1))


def _make_thumb(src: Path, dst: Path, w: int, h: int) -> None:
    # sips -Z upscales smaller images, so only resize when the source is bigger than the thumb.
    if max(w, h) > 480:
        _run([SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "70", "-Z", "480", str(src), "--out", str(dst)], 30)
    else:
        shutil.copyfile(src, dst)


def process_photo(item: Item) -> dict:
    """Synchronous photo pipeline. Returns {file, thumb, bytes}; on failure cleans up and raises 422."""
    uid, tmp = item.uid, config.JOURNAL_TMP
    try:
        r = _run([SIPS, "-g", "pixelWidth", "-g", "pixelHeight", str(item.src)], 30).stdout.decode("utf-8", "replace")
        w, h = _sips_int(r, "pixelWidth"), _sips_int(r, "pixelHeight")
        jpg, thumb = tmp / f"{uid}.jpg", tmp / f"{uid}_thumb.jpg"
        args = [SIPS, "-s", "format", "jpeg", "-s", "formatOptions", "85"]
        if max(w, h) > 2000:
            args += ["-Z", "2000"]
        _run(args + [str(item.src), "--out", str(jpg)], 30)
        _make_thumb(jpg, thumb, w, h)
        config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        size = jpg.stat().st_size
        os.replace(jpg, config.JOURNAL_DIR / jpg.name)
        os.replace(thumb, config.JOURNAL_DIR / thumb.name)
    except (MediaError, OSError):
        unlink(config.JOURNAL_DIR / f"{uid}.jpg", config.JOURNAL_DIR / f"{uid}_thumb.jpg")
        cleanup_uid(uid)
        raise HTTPException(422, "Could not process this photo.")
    unlink(item.src)
    return {"file": f"{uid}.jpg", "thumb": f"{uid}_thumb.jpg", "bytes": size}


def insert_media(c, entry_id: int, position: int, item: Item, photo: Optional[dict] = None) -> int:
    """Insert a media row without committing. Photos arrive ready; video and audio queue for the worker."""
    ts = now_iso()
    if photo:
        cur = c.execute(
            "INSERT INTO journal_media(entry_id, position, kind, status, uid, file, thumb, bytes, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (entry_id, position, "photo", "ready", item.uid, photo["file"], photo["thumb"], photo["bytes"], ts, ts))
    else:
        cur = c.execute(
            "INSERT INTO journal_media(entry_id, position, kind, status, uid, src_path, transcript_status, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (entry_id, position, item.kind, "processing", item.uid, str(item.src), "pending", ts, ts))
    return cur.lastrowid


# ---- worker ----

def _ff(*args) -> list[str]:
    return [config.settings().FFMPEG_BIN, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]


def _encode(row) -> dict:
    uid, tmp, src = row["uid"], config.JOURNAL_TMP, row["src_path"]
    tmp.mkdir(parents=True, exist_ok=True)
    out = {"poster": None, "thumb": None, "width": None, "height": None}
    if row["kind"] == "video":
        mp4 = tmp / f"{uid}.mp4"
        _run(_ff("-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-t", MAX_VIDEO_SECONDS,
                 "-vf", "scale=w='min(1280,iw)':h='min(1280,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-profile:v", "high", "-level:v", "4.0",
                 "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+faststart", mp4), 300)
        info = probe(mp4)
        out.update(file=mp4.name, width=info["width"], height=info["height"], dur=info["duration"], has_audio=info["has_audio"])
        try:  # poster and thumb are optional
            poster, thumb = tmp / f"{uid}_poster.jpg", tmp / f"{uid}_thumb.jpg"
            ss = min(1.0, info["duration"] / 2) if info["duration"] else 0
            _run(_ff("-ss", ss, "-i", mp4, "-frames:v", 1, "-q:v", 3, poster), 30)
            _make_thumb(poster, thumb, info["width"] or 0, info["height"] or 0)
            out.update(poster=poster.name, thumb=thumb.name)
        except (MediaError, OSError):
            unlink(tmp / f"{uid}_poster.jpg", tmp / f"{uid}_thumb.jpg")
    else:
        m4a = tmp / f"{uid}.m4a"
        _run(_ff("-i", src, "-vn", "-map", "0:a:0", "-t", MAX_AUDIO_SECONDS, "-c:a", "aac", "-b:a", "96k", "-ac", 1,
                 "-movflags", "+faststart", m4a), 120)
        info = probe(m4a)
        out.update(file=m4a.name, dur=info["duration"], has_audio=True)
    return out


def encode_job(row) -> None:
    uid, src = row["uid"], row["src_path"]
    placed: list[Path] = []
    c = db.connect()
    try:
        try:
            r = _encode(row)
            config.JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            for name in (r["file"], r["thumb"], r["poster"]):
                if name:
                    os.replace(config.JOURNAL_TMP / name, config.JOURNAL_DIR / name)
                    placed.append(config.JOURNAL_DIR / name)
            size = (config.JOURNAL_DIR / r["file"]).stat().st_size
            cur = c.execute(
                "UPDATE journal_media SET status='ready', file=?, thumb=?, poster=?, width=?, height=?, duration_ms=?, bytes=?, "
                "transcript_status=?, src_path=NULL, updated_at=? WHERE id=? AND status='processing'",
                (r["file"], r["thumb"], r["poster"], r["width"], r["height"],
                 int(r["dur"] * 1000) if r["dur"] is not None else None, size,
                 "pending" if r["has_audio"] else "none", now_iso(), row["id"]))
            c.commit()
            if cur.rowcount == 0:  # entry deleted meanwhile
                unlink(*placed)
        except (MediaError, OSError) as e:  # an encode failure must end the job, or the worker would retry it forever
            unlink(*placed)
            log.warning("encode failed for media %s: %s", row["id"], e)
            c.execute("UPDATE journal_media SET status='error', error=?, transcript_status='none', src_path=NULL, updated_at=? "
                      "WHERE id=? AND status='processing'", (str(e)[:300], now_iso(), row["id"]))
            c.commit()
        # Not in `finally`: a sqlite error (e.g. "database is locked") propagates with the placed files and the source
        # intact, and the worker loop retries the job.
        unlink(src)
        cleanup_uid(uid)
    finally:
        c.close()


def parse_whisper(raw: bytes) -> tuple[str, Optional[str]]:
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        parts = [s.get("text", "").strip() for s in data.get("transcription") or []]
        text = " ".join(p for p in parts if p and not NOISE_SEGMENT.match(p))
        return re.sub(r"\s+", " ", text).strip(), (data.get("result") or {}).get("language")
    except (ValueError, AttributeError, TypeError):
        raise MediaError("could not read whisper output")


def _transcribe(row) -> tuple[str, Optional[str]]:
    s = config.settings()
    if not (s.WHISPER_MODEL and Path(s.WHISPER_BIN).is_file() and Path(s.WHISPER_MODEL).is_file()):
        raise MediaError("Transcription is not configured on the Mac.")
    tmp, uid = config.JOURNAL_TMP, row["uid"]
    tmp.mkdir(parents=True, exist_ok=True)
    wav = tmp / f"{uid}.wav"
    _run(_ff("-i", config.JOURNAL_DIR / row["file"], "-vn", "-map", "0:a:0", "-ac", 1, "-ar", 16000, "-c:a", "pcm_s16le", wav), 120)
    _run([s.WHISPER_BIN, "-m", s.WHISPER_MODEL, "-f", str(wav), "-l", "auto", "-t", "4", "-np", "-nt", "-oj",
          "-of", str(tmp / f"{uid}.whisper")], 600)
    return parse_whisper((tmp / f"{uid}.whisper.json").read_bytes())


def transcribe_job(row) -> None:
    c = db.connect()
    try:
        try:
            text, lang = _transcribe(row)
            c.execute("UPDATE journal_media SET transcript_status='done', transcript_text=?, transcript_lang=?, transcript_error=NULL, "
                      "updated_at=? WHERE id=? AND transcript_status='pending'", (text, lang, now_iso(), row["id"]))
        except Exception as e:
            log.warning("transcription failed for media %s: %s", row["id"], e)
            c.execute("UPDATE journal_media SET transcript_status='error', transcript_error=?, updated_at=? "
                      "WHERE id=? AND transcript_status='pending'", (str(e)[:300], now_iso(), row["id"]))
        c.commit()
    finally:
        c.close()
        unlink(config.JOURNAL_TMP / f"{row['uid']}.wav", config.JOURNAL_TMP / f"{row['uid']}.whisper.json")


def process_next() -> bool:
    c = db.connect()
    try:
        enc = c.execute("SELECT * FROM journal_media WHERE status='processing' ORDER BY id LIMIT 1").fetchone()
        tr = None if enc else c.execute(
            "SELECT * FROM journal_media WHERE status='ready' AND transcript_status='pending' ORDER BY id LIMIT 1").fetchone()
    finally:
        c.close()
    if enc:
        encode_job(enc)
    elif tr:
        transcribe_job(tr)
    return bool(enc or tr)


async def worker_loop():
    while True:
        try:
            did = await asyncio.to_thread(process_next)
        except Exception:
            log.exception("journal worker step failed")
            did = False
        if not did:
            await asyncio.sleep(2)


def recover() -> None:
    """Run once at startup, before requests: fail lost uploads, delete leftover tmp files."""
    config.JOURNAL_TMP.mkdir(parents=True, exist_ok=True)
    c = db.connect()
    try:
        for r in c.execute("SELECT id, src_path FROM journal_media WHERE status='processing'").fetchall():
            if not (r["src_path"] and Path(r["src_path"]).exists()):
                c.execute("UPDATE journal_media SET status='error', error='Upload was lost when the bridge restarted.', "
                          "transcript_status='none', src_path=NULL, updated_at=? WHERE id=?", (now_iso(), r["id"]))
        c.commit()
        keep = {Path(r["src_path"]) for r in c.execute("SELECT src_path FROM journal_media WHERE status='processing'")}
    finally:
        c.close()
    for p in config.JOURNAL_TMP.iterdir():
        if p not in keep:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                unlink(p)
