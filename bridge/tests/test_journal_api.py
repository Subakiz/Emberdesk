import re
from datetime import timedelta

import pytest

from bridge import config, media, moments
from journal_helpers import HEIC, JPEG, MP4, PNG, TEXT, WEBM, probe_json, wib

UID_NAME = re.compile(r"[0-9a-f]{32}(_thumb|_poster)?\.(jpg|mp4|m4a)")


def jpg(name="a.jpg"):
    return {"file": (name, JPEG, "image/jpeg")}


def journal_files():
    d = config.JOURNAL_DIR
    return sorted(p.name for p in d.iterdir() if p.is_file()) if d.exists() else []


def tmp_files():
    return sorted(p.name for p in config.JOURNAL_TMP.iterdir()) if config.JOURNAL_TMP.exists() else []


def new_entry(client, text="hello"):
    r = client.post("/api/journal/entries", json={"text": text})
    assert r.status_code == 201
    return r.json()


@pytest.fixture
def opened(client, clock, rng):
    """A day whose Moment is open (ping at 10:00, clock at 10:00)."""
    rng.v = 3600
    clock.t = wib(26, 5, 30)
    client.get("/api/journal/moment")
    clock.t = wib(26, 10, 0)
    return client


# ---- entries ----

def test_entry_crud_and_pagination(client, clock):
    clock.t = wib(26, 14, 3, 11)
    e = new_entry(client, "Walked to campus")
    assert e["kind"] == "free" and e["day"] == "2026-10-26" and e["created_at"] == "2026-10-26T14:03:11+07:00"
    assert e["media"] == [] and e["moment"] is None
    assert client.get(f"/api/journal/entries/{e['id']}").json()["text"] == "Walked to campus"
    clock.t = wib(26, 14, 5, 40)
    p = client.patch(f"/api/journal/entries/{e['id']}", json={"text": "new"}).json()
    assert p["text"] == "new" and p["updated_at"] == "2026-10-26T14:05:40+07:00"
    for i in range(4):
        new_entry(client, f"e{i}")
    page = client.get("/api/journal?limit=3").json()
    assert [x["text"] for x in page["entries"]] == ["e3", "e2", "e1"] and page["next_before_id"] == page["entries"][-1]["id"]
    rest = client.get(f"/api/journal?limit=3&before_id={page['next_before_id']}").json()
    assert [x["text"] for x in rest["entries"]] == ["e0", "new"] and rest["next_before_id"] is None
    assert client.get("/api/journal?kind=moment").json()["entries"] == []
    for bad in ("limit=0", "limit=101", "kind=nope", "before_id=x"):
        assert client.get(f"/api/journal?{bad}").status_code == 422
    assert client.delete(f"/api/journal/entries/{e['id']}").status_code == 204
    assert client.get(f"/api/journal/entries/{e['id']}").status_code == 404
    assert client.delete(f"/api/journal/entries/{e['id']}").status_code == 404


def test_entry_validation(client):
    assert client.post("/api/journal/entries", json={}).json()["text"] == ""
    assert client.post("/api/journal/entries", json={"text": "x" * 20001}).status_code == 422
    assert client.patch("/api/journal/entries/999", json={"text": "x"}).status_code == 404
    e = new_entry(client)
    assert client.patch(f"/api/journal/entries/{e['id']}", json={"text": "x" * 20001}).status_code == 422


def test_free_day_uses_0500_boundary(client, clock):
    clock.t = wib(27, 4, 59, 59)
    assert new_entry(client)["day"] == "2026-10-26"
    clock.t = wib(27, 5, 0, 0)
    assert new_entry(client)["day"] == "2026-10-27"


# ---- upload guards ----

def test_text_file_is_415(client, tools):
    e = new_entry(client)
    r = client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("a.jpg", TEXT, "image/jpeg")})
    assert r.status_code == 415 and tmp_files() == []


def test_content_length_header_precheck(client, monkeypatch):
    e = new_entry(client)
    monkeypatch.setattr(media, "MAX_UPLOAD", 1000)
    big = {"content-length": str(1000 + media.MiB + 1)}
    r = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg(), headers=big)
    assert r.status_code == 413 and r.json() == {"detail": "File larger than 150 MB."}
    assert client.post("/api/journal/moment", files=jpg(), headers=big).status_code == 413
    assert tmp_files() == []


def chunked_body(boundary="xBOUNDARYx"):
    yield (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.jpg"\r\n'
           "Content-Type: image/jpeg\r\n\r\n").encode()
    yield JPEG
    for _ in range(5):
        yield b"0" * 400
    yield f"\r\n--{boundary}--\r\n".encode()


def test_streamed_oversize_is_413_and_leaves_nothing(client, monkeypatch, tools):
    e = new_entry(client)
    monkeypatch.setattr(media, "MAX_UPLOAD", 1000)
    # a client that understates Content-Length: only the streamed count can catch it
    r = client.post(f"/api/journal/entries/{e['id']}/media", content=chunked_body(),
                    headers={"content-type": "multipart/form-data; boundary=xBOUNDARYx", "content-length": "500"})
    assert r.status_code == 413
    assert tmp_files() == [] and journal_files() == []


def test_chunked_upload_without_content_length_is_411(client, tools):
    e = new_entry(client)
    ct = {"content-type": "multipart/form-data; boundary=xBOUNDARYx"}
    for url in (f"/api/journal/entries/{e['id']}/media", "/api/journal/moment"):
        r = client.post(url, content=chunked_body(), headers=ct)
        assert r.status_code == 411 and r.json() == {"detail": "Length required."}
    assert tmp_files() == [] and journal_files() == []
    assert client.post("/api/journal/entries", content=chunked_body(), headers={"content-type": "application/json"}).status_code != 411


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none", None])
def test_non_cross_site_requests_pass(client, tools, site):
    e = new_entry(client)
    headers = {"sec-fetch-site": site} if site else {}
    assert client.post(f"/api/journal/entries/{e['id']}/media", files=jpg(), headers=headers).status_code == 201
    assert client.post("/lock-log?app=X", headers=headers).status_code == 204  # iPhone Shortcut: no browser, no header


def test_cross_site_writes_are_403_before_any_side_effect(client, tools, conn):
    e = new_entry(client)
    cs = {"sec-fetch-site": "cross-site"}
    reqs = [client.post(f"/api/journal/entries/{e['id']}/media", files=jpg(), headers=cs),
            client.post("/api/journal/moment", files=jpg(), headers=cs),
            client.post("/api/journal/entries", json={"text": "x"}, headers=cs),
            client.patch(f"/api/journal/entries/{e['id']}", json={"text": "x"}, headers=cs),
            client.delete(f"/api/journal/entries/{e['id']}", headers=cs),
            client.post("/lock-log?app=X", headers=cs),
            client.post("/api/push/subscribe", json={"endpoint": "https://web.push.apple.com/a", "keys": {"p256dh": "k", "auth": "a"}}, headers=cs)]
    assert [r.status_code for r in reqs] == [403] * len(reqs)
    assert reqs[0].json() == {"detail": "Cross-site requests are not allowed."}
    assert client.get(f"/api/journal/entries/{e['id']}", headers=cs).status_code == 200  # reads are not blocked
    assert conn.execute("SELECT COUNT(*) FROM journal_media").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM lock_log").fetchone()[0] == 0
    assert tmp_files() == [] and journal_files() == []


def test_ids_are_not_reused_after_delete(client, tools):
    a = new_entry(client)
    m1 = client.post(f"/api/journal/entries/{a['id']}/media", files=jpg()).json()
    assert client.delete(f"/api/journal/entries/{a['id']}").status_code == 204
    b = new_entry(client)
    assert b["id"] > a["id"]
    m2 = client.post(f"/api/journal/entries/{b['id']}/media", files=jpg()).json()
    assert client.delete(f"/api/journal/media/{m2['id']}").status_code == 204
    m3 = client.post(f"/api/journal/entries/{b['id']}/media", files=jpg()).json()
    assert m1["id"] < m2["id"] < m3["id"]


def test_client_filename_is_ignored(client, tools):
    e = new_entry(client)
    r = client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("../../evil.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201
    m = r.json()
    assert UID_NAME.fullmatch(m["url"].rsplit("/", 1)[1]) and "evil" not in m["url"]
    assert all(UID_NAME.fullmatch(n) for n in journal_files())


def test_eleventh_attachment_is_409(client, tools):
    e = new_entry(client)
    for i in range(10):
        assert client.post(f"/api/journal/entries/{e['id']}/media", files=jpg()).status_code == 201
    r = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    assert r.status_code == 409 and r.json()["detail"] == "An entry holds at most 10 attachments."
    assert [m["position"] for m in client.get(f"/api/journal/entries/{e['id']}").json()["media"]] == list(range(10))


def test_media_route_rejects_moment_entry(opened, tools):
    r = opened.post("/api/journal/moment", files=jpg())
    eid = r.json()["entry"]["id"]
    assert opened.post(f"/api/journal/entries/{eid}/media", files=jpg()).status_code == 409
    mid = r.json()["entry"]["media"][0]["id"]
    assert opened.delete(f"/api/journal/media/{mid}").status_code == 409
    assert opened.post("/api/journal/entries/999/media", files=jpg()).status_code == 404


# ---- photo ----

def test_photo_sips_sequence(client, tools):
    e = new_entry(client)
    tools.sips_size = (3000, 2000)
    m = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg()).json()
    assert m["status"] == "ready" and m["kind"] == "photo" and m["transcript"] is None
    assert m["thumb_url"] and m["poster_url"] is None and m["width"] is None and m["bytes"] == len(b"jpegdata")
    convert, thumb = [c for c in tools.calls if "-g" not in c][:2]
    assert convert[0] == "/usr/bin/sips" and "-Z" in convert and convert[convert.index("-Z") + 1] == "2000"
    assert "formatOptions" in convert and convert[convert.index("formatOptions") + 1] == "85"
    assert thumb[thumb.index("-Z") + 1] == "480" and thumb[thumb.index("formatOptions") + 1] == "70"
    assert all(isinstance(a, str) for c in tools.calls for a in c)
    assert len(journal_files()) == 2 and tmp_files() == []


def test_photo_small_is_not_upscaled(client, tools):
    e = new_entry(client)
    tools.sips_size = (1200, 800)
    client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    convert = [c for c in tools.calls if "-g" not in c][0]
    assert "-Z" not in convert
    tools.calls.clear()
    tools.sips_size = (400, 300)  # thumb is a plain copy, no second sips
    client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    assert len([c for c in tools.calls if "-g" not in c]) == 1


@pytest.mark.parametrize("data", [HEIC, PNG])
def test_photo_formats_accepted(client, tools, data):
    e = new_entry(client)
    assert client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("p", data, "x/y")}).status_code == 201


def test_photo_sips_failure_is_422_and_clean(client, tools):
    e = new_entry(client)
    tools.fail.add("sips")
    r = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    assert r.status_code == 422 and r.json()["detail"] == "Could not process this photo."
    assert journal_files() == [] and tmp_files() == []
    assert client.get(f"/api/journal/entries/{e['id']}").json()["media"] == []


def test_free_entry_delete_removes_files(client, tools):
    e = new_entry(client)
    client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    client.post(f"/api/journal/entries/{e['id']}/media", files=jpg())
    assert len(journal_files()) == 4
    assert client.delete(f"/api/journal/entries/{e['id']}").status_code == 204
    assert journal_files() == []


def test_delete_single_media(client, tools):
    e = new_entry(client)
    m = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg()).json()
    assert client.get(f"/api/journal/media/{m['id']}").json()["id"] == m["id"]
    assert client.delete(f"/api/journal/media/{m['id']}").status_code == 204
    assert journal_files() == [] and client.delete(f"/api/journal/media/{m['id']}").status_code == 404


# ---- video / audio intake ----

def test_video_upload_queues_processing(client, tools):
    e = new_entry(client)
    tools.probe_src = probe_json(duration=14.2)
    r = client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("v.mov", MP4, "video/quicktime")})
    m = r.json()
    assert r.status_code == 201 and m["kind"] == "video" and m["status"] == "processing"
    assert m["url"] is None and m["thumb_url"] is None and m["poster_url"] is None
    assert m["transcript"] == {"status": "pending", "text": None, "language": None, "error": None}
    assert len(tmp_files()) == 1  # the raw upload waits for the worker


def test_audio_upload_and_webm(client, tools):
    e = new_entry(client)
    tools.probe_src = probe_json(duration=None, video=False)  # MediaRecorder WebM often has no duration
    m = client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("voice.webm", WEBM, "audio/webm")}).json()
    assert m["kind"] == "audio" and m["status"] == "processing" and m["thumb_url"] is None


def test_attached_pic_counts_as_audio(client, tools):
    e = new_entry(client)
    tools.probe_src = probe_json(duration=30, video=True, attached_pic=True)
    assert client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("a.m4a", MP4, "audio/mp4")}).json()["kind"] == "audio"


def test_duration_caps_and_unreadable(client, tools):
    e = new_entry(client)
    url = f"/api/journal/entries/{e['id']}/media"
    tools.probe_src = probe_json(duration=75)
    r = client.post(url, files={"file": ("v.mp4", MP4, "video/mp4")})
    assert r.status_code == 422 and r.json()["detail"] == "Videos can be at most 60 seconds."
    tools.probe_src = probe_json(duration=61.0)  # within the 1 s tolerance
    assert client.post(url, files={"file": ("v.mp4", MP4, "video/mp4")}).status_code == 201
    tools.probe_src = probe_json(duration=700, video=False)
    assert client.post(url, files={"file": ("v.m4a", MP4, "audio/mp4")}).status_code == 422
    tools.probe_src = probe_json(video=False, audio=False)
    assert client.post(url, files={"file": ("v.m4a", MP4, "audio/mp4")}).json()["detail"] == "No playable audio or video found."
    tools.fail.add("ffprobe")
    r = client.post(url, files={"file": ("v.m4a", MP4, "audio/mp4")})
    assert r.status_code == 422 and r.json()["detail"] == "Could not read this file."
    assert len(tmp_files()) == 1  # only the accepted video's upload is left


def test_missing_ffprobe_is_503(client, monkeypatch):
    def gone(args, timeout):
        raise media.BinaryMissing("ffprobe is not installed")

    monkeypatch.setattr(media, "_run", gone)
    e = new_entry(client)
    r = client.post(f"/api/journal/entries/{e['id']}/media", files={"file": ("v.mp4", MP4, "video/mp4")})
    assert r.status_code == 503 and r.json()["detail"] == "ffmpeg is not installed on the Mac."
    assert tmp_files() == []


# ---- Moment ----

def test_moment_before_ping_is_409_and_stores_nothing(client, clock, rng, tools):
    rng.v = 3600
    clock.t = wib(26, 5, 30)
    client.get("/api/journal/moment")
    r = client.post("/api/journal/moment", files=jpg())
    assert r.status_code == 409 and r.json()["detail"] == "Your Moment is not open yet."
    assert client.get("/api/journal").json()["entries"] == [] and journal_files() == [] and tools.calls == []


def test_moment_on_time_and_late(opened, clock, tools):
    clock.t = wib(26, 10, 29)
    s = opened.post("/api/journal/moment", files=jpg()).json()
    assert s["state"] == "submitted_on_time" and s["on_time"] is True and s["submitted_at"] == "2026-10-26T10:29:00+07:00"
    assert s["entry"]["kind"] == "moment" and s["entry"]["moment"] == {
        "date": "2026-10-26", "submitted_at": "2026-10-26T10:29:00+07:00", "on_time": True}
    assert s["streak"]["current"] == 1 and s["streak"]["today_counted"]


def test_moment_late(opened, clock, tools):
    clock.t = wib(26, 10, 31)
    s = opened.post("/api/journal/moment", files=jpg()).json()
    assert s["state"] == "submitted_late" and s["on_time"] is False and s["streak"]["current"] == 1


def test_moment_on_time_reference_is_fired_at_when_sent(opened, clock, tools, conn):
    conn.execute("UPDATE moment_days SET fired_at=?, push_result='sent'", (moments.iso(wib(26, 13, 0)),))
    conn.commit()
    clock.t = wib(26, 13, 29)
    s = opened.post("/api/journal/moment", files=jpg()).json()
    assert s["state"] == "submitted_on_time"


def test_moment_audio_is_415(opened, tools):
    tools.probe_src = probe_json(video=False)
    r = opened.post("/api/journal/moment", files={"file": ("v.m4a", MP4, "audio/mp4")})
    assert r.status_code == 415 and r.json()["detail"] == "A Moment is one photo or one video."
    assert tmp_files() == []


def test_moment_caption_and_replace(opened, clock, tools, conn):
    clock.t = wib(26, 10, 20)
    first = opened.post("/api/journal/moment", files=jpg(), data={"caption": "nice light"}).json()
    old_files = set(journal_files())
    assert first["entry"]["text"] == "nice light" and len(old_files) == 2
    clock.t = wib(26, 11, 0)  # a replacement long after the on-time window keeps the first verdict
    second = opened.post("/api/journal/moment", files=jpg()).json()
    # SQLite may reuse the freed rowid for the new entry; the media files are what must differ.
    assert second["entry"]["media"][0]["url"] != first["entry"]["media"][0]["url"]
    assert second["state"] == "submitted_on_time" and second["on_time"] is True
    assert second["submitted_at"] == first["submitted_at"] == "2026-10-26T10:20:00+07:00"
    assert second["entry"]["text"] == "nice light"
    assert not (old_files & set(journal_files())) and len(journal_files()) == 2
    assert conn.execute("SELECT COUNT(*) FROM journal_entries WHERE kind='moment'").fetchone()[0] == 1
    assert second["streak"]["current"] == 1 and second["streak"]["best"] == 1
    third = opened.post("/api/journal/moment", files=jpg(), data={"caption": "new caption"}).json()
    assert third["entry"]["text"] == "new caption"


def test_moment_replace_failure_keeps_old(opened, clock, tools, monkeypatch):
    first = opened.post("/api/journal/moment", files=jpg()).json()
    before = journal_files()

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(media, "insert_media", boom)
    with pytest.raises(RuntimeError):
        opened.post("/api/journal/moment", files=jpg())
    assert opened.get(f"/api/journal/entries/{first['entry']['id']}").json()["text"] == ""
    assert journal_files() == before and tmp_files() == []


def test_delete_moment_uncounts_and_resubmit_is_new_first(opened, clock, tools, conn):
    clock.t = wib(26, 10, 5)
    first = opened.post("/api/journal/moment", files=jpg()).json()
    assert opened.delete(f"/api/journal/entries/{first['entry']['id']}").status_code == 204
    row = conn.execute("SELECT * FROM moment_days").fetchone()
    assert row["entry_id"] is None and row["submitted_at"] is None and row["on_time"] is None
    s = opened.get("/api/journal/moment").json()
    assert s["state"] == "pinged" and s["streak"]["current"] == 0 and journal_files() == []
    clock.t = wib(26, 12, 0)  # late now: a new first submission, new verdict
    s = opened.post("/api/journal/moment", files=jpg()).json()
    assert s["state"] == "submitted_late" and s["submitted_at"] == "2026-10-26T12:00:00+07:00"


def test_moment_closes_at_0500(opened, clock, tools):
    opened.post("/api/journal/moment", files=jpg())
    clock.t = wib(27, 5, 0)
    s = opened.get("/api/journal/moment").json()
    assert s["date"] == "2026-10-27" and s["state"] == "no_ping_yet"
    assert opened.post("/api/journal/moment", files=jpg()).status_code == 409
    days = opened.get("/api/journal/moments?days=5").json()["days"]
    assert [d["date"] for d in days] == ["2026-10-26", "2026-10-27"] and days[0]["state"] == "submitted_on_time"
    assert days[0]["thumb_url"].endswith("_thumb.jpg")


def test_moments_days_validation(client):
    assert client.get("/api/journal/moments?days=0").status_code == 422
    assert client.get("/api/journal/moments?days=401").status_code == 422
    assert client.get("/api/journal/moments").json() == {"days": []}


# ---- serving ----

def test_serving_and_range(client, tools):
    e = new_entry(client)
    m = client.post(f"/api/journal/entries/{e['id']}/media", files=jpg()).json()
    path = config.JOURNAL_DIR / m["url"].rsplit("/", 1)[1]
    path.write_bytes(bytes(range(256)) * 4)  # 1024 bytes
    r = client.get(m["url"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and len(r.content) == 1024
    assert "immutable" in r.headers["cache-control"]
    r = client.get(m["url"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["content-range"] == "bytes 0-99/1024" and r.headers["accept-ranges"] == "bytes"
    assert len(r.content) == 100
    assert client.get(m["url"], headers={"Range": "bytes=5000-6000"}).status_code == 416
    assert client.head(m["url"]).status_code == 200
    mp4 = "a" * 32 + ".mp4"
    (config.JOURNAL_DIR / mp4).write_bytes(b"v")
    (config.JOURNAL_DIR / ("b" * 32 + ".m4a")).write_bytes(b"a")
    assert client.get("/journal-media/" + mp4).headers["content-type"] == "video/mp4"
    assert client.get("/journal-media/" + "b" * 32 + ".m4a").headers["content-type"] == "audio/mp4"


@pytest.mark.parametrize("name", ["..%2Fapp.sqlite3", "x.jpg", "A" * 32 + ".jpg", "a" * 32 + ".png", "a" * 32 + ".jpg.bak",
                                  "a" * 32 + ".upload", "a" * 31 + ".jpg", "..%2F" + "a" * 32 + ".jpg", "tmp"])
def test_serving_rejects_bad_names(client, name):
    config.JOURNAL_TMP.mkdir(parents=True, exist_ok=True)
    (config.JOURNAL_TMP / ("a" * 32 + ".upload")).write_bytes(b"secret")
    assert client.get("/journal-media/" + name).status_code == 404


def test_status_has_journal_keys(client):
    j = client.get("/api/status").json()["journal"]
    assert set(j) == {"ffmpeg", "whisper", "push_subscriptions"} and j["push_subscriptions"] == 0
    assert j["whisper"] is False  # WHISPER_MODEL unset in tests


def test_settings_roundtrip_keys(client):
    assert set(client.get("/api/journal/settings").json()) == {"window_start", "window_end", "applies_from"}
