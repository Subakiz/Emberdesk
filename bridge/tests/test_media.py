import sqlite3
import sys
from pathlib import Path

import pytest

from bridge import config, db, media
REAL_RUN = media._run  # captured before the autouse guard replaces it
from journal_helpers import HEIC, JPEG, MP4, PNG, WEBM, probe_json


def files(d):
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def new_entry(client):
    return client.post("/api/journal/entries", json={"text": "t"}).json()["id"]


def upload(client, tools, kind="video", eid=None, **probe):
    eid = eid or new_entry(client)
    tools.probe_src = probe_json(video=kind == "video", **probe)
    r = client.post(f"/api/journal/entries/{eid}/media", files={"file": ("f", MP4, "x/y")})
    assert r.status_code == 201, r.text
    return r.json()


def row(mid):
    c = db.connect()
    try:
        return c.execute("SELECT * FROM journal_media WHERE id=?", (mid,)).fetchone()
    finally:
        c.close()


def test_sniff():
    assert media.sniff(b"\xff\xd8\xff\xe0" + b"0" * 12) == "jpg"
    assert media.sniff(b"\x89PNG\r\n\x1a\n" + b"0" * 8) == "png"
    assert media.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    for brand in (b"heic", b"heix", b"mif1", b"msf1", b"hevc"):
        assert media.sniff(b"\x00\x00\x00\x18ftyp" + brand + b"0000") == "heic"
    for brand in (b"isom", b"mp42", b"qt  ", b"M4A "):
        assert media.sniff(b"\x00\x00\x00\x18ftyp" + brand + b"0000") == "isobmff"
    assert media.sniff(b"\x1a\x45\xdf\xa3" + b"0" * 12) == "webm"
    assert media.sniff(b"OggS" + b"0" * 12) == "ogg"
    assert media.sniff(b"hello world, text") is None and media.sniff(b"") is None


# ---- whisper parsing ----

def test_parse_whisper_fixture():
    raw = (Path(__file__).parent / "fixtures" / "whisper.json").read_bytes()
    assert media.parse_whisper(raw) == ("Halo this is a test.", "id")


def test_parse_whisper_drops_noise_and_survives_bad_bytes():
    raw = b'{"result": {"language": "en"}, "transcription": [{"text": " [BLANK_AUDIO]"}, {"text": " (music)"}, {"text": " hi   there"}]}'
    assert media.parse_whisper(raw) == ("hi there", "en")
    assert media.parse_whisper(b'{"transcription": [{"text": "caf\xc3"}]}')[0].startswith("caf")  # split multi-byte char
    assert media.parse_whisper(b'{"transcription": [{"text": "[BLANK_AUDIO]"}]}') == ("", None)
    assert media.parse_whisper(b"{}") == ("", None)
    for bad in (b"not json", b"[1,2]", b'{"transcription": 5}'):
        with pytest.raises(media.MediaError):
            media.parse_whisper(bad)


# ---- worker ----

def test_video_encode_then_transcribe(client, tools, whisper_env):
    m = upload(client, tools, "video", duration=14.2)
    tools.calls.clear()
    tools.probe_out = probe_json(duration=14.23, w=720, h=1280)
    assert media.process_next() is True
    r = row(m["id"])
    assert r["status"] == "ready" and r["src_path"] is None and r["transcript_status"] == "pending"
    assert (r["width"], r["height"], r["duration_ms"]) == (720, 1280, 14230) and r["bytes"] == len(b"encoded-" + b"x" * 100)
    assert r["file"] == r["uid"] + ".mp4" and r["poster"] == r["uid"] + "_poster.jpg" and r["thumb"] == r["uid"] + "_thumb.jpg"
    assert files(config.JOURNAL_DIR) == sorted(["tmp", r["file"], r["poster"], r["thumb"]]) and files(config.JOURNAL_TMP) == []
    enc = tools.calls[0]
    assert tools.names() == ["ffmpeg", "ffprobe", "ffmpeg", "sips"]
    assert enc[1:5] == ["-nostdin", "-hide_banner", "-loglevel", "error"] and "-y" in enc
    assert enc[enc.index("-t") + 1] == "60" and "libx264" in enc and "0:a:0?" in enc and enc[-1].endswith(".mp4")
    assert any(a.startswith("scale=w='min(1280,iw)'") for a in enc)
    poster = tools.calls[2]
    assert poster[poster.index("-ss") + 1] == "1.0" and poster[-1].endswith("_poster.jpg")  # min(1.0, 14.23 / 2)
    tools.calls.clear()
    assert media.process_next() is True
    r = row(m["id"])
    assert (r["transcript_status"], r["transcript_text"], r["transcript_lang"]) == ("done", "Halo this is a test.", "id")
    assert tools.names() == ["ffmpeg", "whisper-cli"]
    w = tools.calls[1]
    assert w[w.index("-l") + 1] == "auto" and "-oj" in w and "-nt" in w and "-np" in w and w[w.index("-t") + 1] == "4"
    assert files(config.JOURNAL_TMP) == []
    assert media.process_next() is False
    out = client.get(f"/api/journal/media/{m['id']}").json()
    assert out["status"] == "ready" and out["transcript"]["status"] == "done" and out["poster_url"].endswith("_poster.jpg")


def test_video_without_audio_has_no_transcript(client, tools):
    m = upload(client, tools, "video")
    tools.probe_out = probe_json(audio=False)
    media.process_next()
    assert row(m["id"])["transcript_status"] == "none" and media.process_next() is False


def test_small_video_thumb_is_copied_not_sips(client, tools):
    m = upload(client, tools, "video")
    tools.probe_out = probe_json(w=320, h=240)
    media.process_next()
    assert "sips" not in tools.names() and row(m["id"])["thumb"]


def test_poster_failure_is_not_fatal(client, tools):
    m = upload(client, tools, "video")
    tools.fail_if = lambda a: "-frames:v" in a
    media.process_next()
    r = row(m["id"])
    assert r["status"] == "ready" and r["poster"] is None and r["thumb"] is None and r["file"]
    assert files(config.JOURNAL_TMP) == []


def test_audio_transcode(client, tools):
    m = upload(client, tools, "audio", duration=30)
    tools.calls.clear()
    media.process_next()
    r = row(m["id"])
    assert r["status"] == "ready" and r["file"].endswith(".m4a") and r["thumb"] is None and r["poster"] is None
    assert r["duration_ms"] == 30000 and r["transcript_status"] == "pending"
    enc = tools.calls[0]
    assert "-vn" in enc and enc[enc.index("-t") + 1] == "600" and enc[enc.index("-ac") + 1] == "1" and enc[-1].endswith(".m4a")
    assert tools.names() == ["ffmpeg", "ffprobe"]


def timed_out(args, timeout):
    raise media.MediaError(f"{Path(args[0]).name} timed out")


@pytest.mark.parametrize("how", ["fail", "timeout"])
def test_encode_failure_marks_error_and_cleans(client, tools, monkeypatch, how):
    m = upload(client, tools, "video")
    if how == "fail":
        tools.fail.add("ffmpeg")
    else:  # a timeout reaches the worker as a MediaError too
        monkeypatch.setattr(media, "_run", timed_out)
    media.process_next()
    r = row(m["id"])
    assert r["status"] == "error" and r["error"] and len(r["error"]) <= 300
    assert r["transcript_status"] == "none" and r["src_path"] is None and r["file"] is None
    assert files(config.JOURNAL_TMP) == [] and files(config.JOURNAL_DIR) == ["tmp"]
    assert media.process_next() is False
    j = client.get(f"/api/journal/media/{m['id']}").json()
    assert j["status"] == "error" and j["url"] is None and j["transcript"]["status"] == "none"


class LockedConn:
    """Delegates to a real connection but fails the success UPDATE like a long writer would."""

    def __init__(self, real):
        self.real = real

    def execute(self, sql, *a):
        if sql.startswith("UPDATE journal_media SET status='ready'"):
            raise sqlite3.OperationalError("database is locked")
        return self.real.execute(sql, *a)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_db_lock_does_not_delete_a_finished_encode(client, tools, monkeypatch):
    m = upload(client, tools, "video")
    src = Path(row(m["id"])["src_path"])
    real_connect = db.connect
    with monkeypatch.context() as mp:
        mp.setattr(media.db, "connect", lambda: LockedConn(real_connect()))
        with pytest.raises(sqlite3.OperationalError):
            media.process_next()
    r = row(m["id"])
    assert r["status"] == "processing" and r["error"] is None  # not flipped to error
    assert src.exists()  # the source survives for the retry
    assert media.process_next() is True  # worker loop retries; the encode is idempotent
    r = row(m["id"])
    assert r["status"] == "ready" and not src.exists() and (config.JOURNAL_DIR / r["file"]).exists()
    assert files(config.JOURNAL_TMP) == []


def test_run_hides_the_data_dir_in_errors():
    bad = str(config.DATA_DIR / "journal" / "tmp" / "x.upload")
    with pytest.raises(media.MediaError) as e:
        REAL_RUN([sys.executable, "-c", "import sys; sys.stderr.write(sys.argv[1] + ': Invalid data'); sys.exit(1)", bad], 10)
    assert str(config.DATA_DIR) not in str(e.value) and "Invalid data" in str(e.value) and "…/journal/tmp/x.upload" in str(e.value)


def test_one_job_at_a_time_encodes_before_transcripts(client, tools, whisper_env):
    eid = new_entry(client)
    a = upload(client, tools, "video", eid=eid)
    b = upload(client, tools, "audio", eid=eid)
    states = []
    for _ in range(5):
        did = media.process_next()
        states.append((did, row(a["id"])["status"], row(a["id"])["transcript_status"], row(b["id"])["status"], row(b["id"])["transcript_status"]))
    assert states == [
        (True, "ready", "pending", "processing", "pending"),
        (True, "ready", "pending", "ready", "pending"),
        (True, "ready", "done", "ready", "pending"),
        (True, "ready", "done", "ready", "done"),
        (False, "ready", "done", "ready", "done")]


def test_transcription_not_configured(client, tools, monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    m = upload(client, tools, "audio")
    media.process_next()
    tools.calls.clear()
    media.process_next()
    r = row(m["id"])
    assert r["transcript_status"] == "error" and r["transcript_error"] == "Transcription is not configured on the Mac."
    assert tools.calls == []


def test_transcription_model_file_missing(client, tools, monkeypatch, tmp_path):
    monkeypatch.setenv("WHISPER_MODEL", str(tmp_path / "nope.bin"))
    m = upload(client, tools, "audio")
    media.process_next()
    media.process_next()
    assert row(m["id"])["transcript_error"] == "Transcription is not configured on the Mac."


def test_whisper_failure_cleans_files(client, tools, whisper_env):
    m = upload(client, tools, "audio")
    media.process_next()
    tools.fail.add("whisper-cli")
    media.process_next()
    r = row(m["id"])
    assert r["transcript_status"] == "error" and "whisper-cli" in r["transcript_error"]
    assert files(config.JOURNAL_TMP) == []


def test_retranscribe(client, tools, whisper_env):
    m = upload(client, tools, "audio")
    assert client.post(f"/api/journal/media/{m['id']}/retranscribe").status_code == 409  # pending
    media.process_next()
    tools.fail.add("whisper-cli")
    media.process_next()
    tools.fail.clear()
    r = client.post(f"/api/journal/media/{m['id']}/retranscribe")
    assert r.status_code == 202 and r.json()["transcript"]["status"] == "pending" and r.json()["transcript"]["error"] is None
    media.process_next()
    assert row(m["id"])["transcript_status"] == "done"
    assert client.post(f"/api/journal/media/{m['id']}/retranscribe").status_code == 202  # done can be redone
    assert client.post("/api/journal/media/999/retranscribe").status_code == 404


def test_retranscribe_photo_and_none_are_409(client, tools):
    eid = new_entry(client)
    photo = client.post(f"/api/journal/entries/{eid}/media", files={"file": ("p.jpg", JPEG, "image/jpeg")}).json()
    assert client.post(f"/api/journal/media/{photo['id']}/retranscribe").status_code == 409
    v = upload(client, tools, "video", eid=eid)
    tools.probe_out = probe_json(audio=False)
    media.process_next()
    assert client.post(f"/api/journal/media/{v['id']}/retranscribe").status_code == 409


def test_delete_while_processing_leaves_no_files(client, tools):
    m = upload(client, tools, "video")
    eid = m["entry_id"]
    assert client.delete(f"/api/journal/entries/{eid}").status_code == 204
    assert files(config.JOURNAL_TMP) == []  # the delete already removed the raw upload
    assert media.process_next() is False  # row is gone, nothing to do


def test_delete_during_encode_removes_outputs(client, tools, monkeypatch):
    m = upload(client, tools, "video")
    eid = m["entry_id"]
    real = tools.__call__

    def delete_midway(args, timeout):
        r = real(args, timeout)
        if Path(args[0]).name == "ffmpeg" and "-frames:v" not in args:
            c = db.connect()  # the user deletes the entry while ffmpeg runs
            c.execute("DELETE FROM journal_entries WHERE id=?", (eid,))
            c.commit()
            c.close()
        return r

    monkeypatch.setattr(media, "_run", delete_midway)
    assert media.process_next() is True
    assert files(config.JOURNAL_DIR) == ["tmp"] and files(config.JOURNAL_TMP) == []


# ---- recover ----

def test_recover(client, tools):
    keep = upload(client, tools, "video")
    lost = upload(client, tools, "audio")
    Path(row(lost["id"])["src_path"]).unlink()
    (config.JOURNAL_TMP / "orphan.mp4").write_bytes(b"x")
    (config.JOURNAL_TMP / ("f" * 32 + ".wav")).write_bytes(b"x")
    media.recover()
    assert row(lost["id"])["status"] == "error" and row(lost["id"])["error"] == "Upload was lost when the bridge restarted."
    assert row(lost["id"])["transcript_status"] == "none" and row(lost["id"])["src_path"] is None
    assert row(keep["id"])["status"] == "processing"
    assert files(config.JOURNAL_TMP) == [Path(row(keep["id"])["src_path"]).name]


# ---- config ----

def test_config_binaries_fall_back_to_absolute_paths(monkeypatch):
    for k in ("FFMPEG_BIN", "FFPROBE_BIN", "WHISPER_BIN", "WHISPER_MODEL", "VAPID_SUBJECT", "PUBLIC_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    s = config.settings()
    assert s.FFMPEG_BIN == "/opt/homebrew/bin/ffmpeg" and s.FFPROBE_BIN == "/opt/homebrew/bin/ffprobe"
    assert s.WHISPER_BIN == "/opt/homebrew/bin/whisper-cli" and s.WHISPER_MODEL is None
    assert s.VAPID_SUBJECT.startswith("mailto:")
    monkeypatch.setenv("PUBLIC_URL", "https://mac.ts.net/")
    assert config.settings().VAPID_SUBJECT == "https://mac.ts.net"


def test_whisper_model_defaults_to_the_homebrew_path_only_when_the_file_exists(monkeypatch, tmp_path):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)  # conftest points HOME at tmp_path
    assert config.settings().WHISPER_MODEL is None
    model = tmp_path / ".local/share/whisper-cpp/models/ggml-large-v3-turbo-q5_0.bin"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"")
    assert config.settings().WHISPER_MODEL == str(model)
    monkeypatch.setenv("WHISPER_MODEL", "/elsewhere/m.bin")
    assert config.settings().WHISPER_MODEL == "/elsewhere/m.bin"
