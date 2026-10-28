import io
from datetime import timedelta

import pytest

from bridge import ai, config, db, jobs

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"0" * 64


def upload(client, task_id, data, name="p.jpg"):
    return client.post(f"/api/tasks/{task_id}/proof", files={"photo": (name, io.BytesIO(data), "image/jpeg")})


@pytest.fixture
def task(conn):
    cur = conn.execute("INSERT INTO tasks(date, slot, kind, text) VALUES('2026-10-22', 1, 'physical', 'Take a walk')")
    conn.commit()
    return cur.lastrowid


@pytest.fixture
def judge(monkeypatch):
    calls = []

    def fake(text, path):
        calls.append((text, path))
        return {"pass": True, "reason": "looks fine"}

    monkeypatch.setattr(ai, "judge_photo", fake)
    return calls


def test_status(client):
    j = client.get("/api/status").json()
    assert j["ok"] and j["last_job"] is None and "public_url" in j


def test_day_and_week(client):
    d = client.get("/api/day/2026-10-26").json()  # a Monday
    assert d["date"] == "2026-10-26" and d["occurrences"] and d["tasks"] == []
    assert set(d["occurrences"][0]) >= {"id", "course_name", "can_cancel", "has_materials", "briefing_md"}
    w = client.get("/api/week/2026-10-26").json()
    assert [x["date"] for x in w["days"]][:2] == ["2026-10-26", "2026-10-27"] and len(w["days"]) == 7
    assert client.get("/api/day/nope").status_code == 422


def test_cancel_rules(client):
    occ = client.get("/api/day/2099-01-05").json()["occurrences"][0]  # future: allowed
    assert occ["can_cancel"]
    r = client.post(f"/api/occurrences/{occ['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.post(f"/api/occurrences/{occ['id']}/uncancel").json()["status"] == "scheduled"
    past = client.get("/api/day/2020-01-06").json()["occurrences"][0]
    assert client.post(f"/api/occurrences/{past['id']}/cancel").status_code == 409
    assert client.post("/api/occurrences/99999/cancel").status_code == 404


def test_pengganti_and_brief(client, monkeypatch):
    briefed = []
    monkeypatch.setattr(jobs, "brief_occurrence", lambda conn, oid: briefed.append(oid) or True)
    code = client.get("/api/courses").json()[0]["code"]
    body = {"course_code": code, "kind": "K", "start": "08:00", "end": "09:40", "room": "X"}
    tomorrow = (config.now().date() + timedelta(days=1)).isoformat()
    r = client.post("/api/occurrences", json={**body, "date": tomorrow})
    assert r.status_code == 201 and r.json()["is_pengganti"]
    assert briefed == [r.json()["id"]]
    far = client.post("/api/occurrences", json={**body, "date": "2099-01-01"})
    assert far.status_code == 201 and len(briefed) == 1
    for bad in ({"course_code": "NOPE"}, {"kind": "X"}, {"date": "01-01-2099"}, {"start": "9am"}, {"end": "07:00"}):
        assert client.post("/api/occurrences", json={**body, "date": "2099-01-01", **bad}).status_code in (404, 422)
    r = client.post(f"/api/occurrences/{far.json()['id']}/brief")
    assert r.status_code == 202 and briefed[-1] == far.json()["id"]
    assert client.get(f"/api/occurrences/{far.json()['id']}").json()["id"] == far.json()["id"]


def test_courses(client):
    cs = client.get("/api/courses").json()
    mbl = next(c for c in cs if c["code"] == "MAT2111")
    assert set(mbl["material_weeks"]) == {"K", "R"} and mbl["sessions"]
    assert client.patch("/api/courses/MAT2111", json={"start_week": 3}).json()["start_week"] == 3
    assert client.patch("/api/courses/MAT2111", json={"start_week": 0}).status_code == 422
    assert client.patch("/api/courses/NOPE", json={"start_week": 1}).status_code == 404


def test_proof_pass_and_photo_serving(client, task, judge):
    r = upload(client, task, JPEG)
    assert r.status_code == 200
    t = r.json()
    assert t["status"] == "passed" and t["verdict_reason"] == "looks fine"
    assert t["reveal_at"] == "2026-10-22T06:00:00+07:00" and t["photo_url"].startswith("/photos/2026-10-22_1_")
    got = client.get(t["photo_url"])
    assert got.status_code == 200 and got.content == JPEG
    assert upload(client, task, JPEG).status_code == 409  # already passed


def test_proof_validation(client, task, judge):
    assert upload(client, task, b"GIF89a" + b"0" * 50).status_code == 415
    assert upload(client, task, JPEG + b"0" * (10 * 1024 * 1024)).status_code == 413
    assert upload(client, 999, JPEG).status_code == 404
    assert judge == []


def test_proof_oversize_rejected_by_header(client, task, judge):
    r = client.post(f"/api/tasks/{task}/proof", content=b"x", headers={"content-length": str(50 * 1024 * 1024),
                                                                          "content-type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413


def test_proof_does_not_overwrite_pass(client, conn, task, monkeypatch):
    # A concurrent upload passed while this one was being judged.
    def judge_fail(text, path):
        c2 = db.connect()  # handler runs in another thread
        c2.execute("UPDATE tasks SET status='passed', photo_path='first.jpg' WHERE id=?", (task,))
        c2.commit()
        c2.close()
        return {"pass": False, "reason": "late fail"}

    monkeypatch.setattr(ai, "judge_photo", judge_fail)
    t = upload(client, task, JPEG).json()
    assert t["status"] == "passed" and "first.jpg" in (conn.execute("SELECT photo_path FROM tasks").fetchone()[0])


def test_proof_png_and_heic(client, task, monkeypatch):
    seen = []
    monkeypatch.setattr(ai, "judge_photo", lambda text, path: seen.append(str(path)) or {"pass": False, "reason": "no"})
    assert upload(client, task, PNG).json()["status"] == "failed"
    assert seen[-1].endswith(".png")
    import subprocess

    def fake_sips(cmd, **kw):
        open(cmd[-1], "wb").write(JPEG)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_sips)
    t = upload(client, task, HEIC, "p.heic").json()
    assert seen[-1].endswith(".jpg") and t["photo_url"].endswith(".jpg")


def test_proof_ai_error_keeps_photo(client, task, monkeypatch):
    def boom(text, path):
        raise ai.AIError("claude down")

    monkeypatch.setattr(ai, "judge_photo", boom)
    r = upload(client, task, JPEG)
    assert r.status_code == 502 and "claude down" in r.json()["detail"]
    day = client.get("/api/day/2026-10-22").json()["tasks"][0]
    assert day["status"] == "pending" and day["photo_url"]
    assert client.get(day["photo_url"]).status_code == 200


def test_appeal(client, conn, task):
    assert client.post(f"/api/tasks/{task}/appeal", json={"note": "x"}).status_code == 409  # pending
    conn.execute("UPDATE tasks SET status='failed' WHERE id=?", (task,))
    conn.commit()
    t = client.post(f"/api/tasks/{task}/appeal", json={"note": "it was a walk"}).json()
    assert t["status"] == "passed" and t["appealed"] and t["appeal_note"] == "it was a walk"
    assert client.post(f"/api/tasks/{task}/appeal", json={"note": "again"}).status_code == 409


def test_photo_traversal(client, conn):
    config.PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    (config.DATA_DIR / "secret.txt").write_text("s")
    for p in ("/photos/..%2Fsecret.txt", "/photos/%2e%2e%2fsecret.txt", "/photos/nope.jpg", "/photos/a b.jpg"):
        assert client.get(p).status_code == 404, p


def test_notes_and_progress(client, conn, task):
    assert client.put("/api/notes/2026-10-22", json={"text": "learned x"}).json() == {"date": "2026-10-22", "text": "learned x"}
    client.put("/api/notes/2026-10-22", json={"text": "learned y"})
    conn.execute("INSERT INTO summaries VALUES(?, 'sum')", (config.today(),))
    conn.execute("INSERT INTO ypt_days(date, total_minutes, subjects_json) VALUES(?, 90, '{\"Math\": 90}')", (config.today(),))
    conn.commit()
    p = client.get("/api/progress?days=3650").json()
    assert p["notes"] == [{"date": "2026-10-22", "text": "learned y"}]
    assert p["summaries"][0]["text"] == "sum" and p["ypt"][0]["subjects"] == {"Math": 90}
    assert p["heatmap"] == [{"date": "2026-10-22", "passed": 0, "total": 1}]


def test_quotes(client, conn):
    conn.execute("DELETE FROM quotes")
    for i in range(3):
        conn.execute("INSERT INTO quotes(text, author) VALUES(?, 'A')", (f"q{i}",))
    conn.commit()
    expected = (config.now().date() - __import__("datetime").date(2026, 1, 1)).days % 3
    assert client.get("/api/quotes/today").json()["text"] == f"q{expected}"
    assert client.get("/api/day/2026-01-02").json()["quote"]["text"] == "q1"
    q = client.post("/api/quotes", json={"text": "mine", "author": "me"}).json()
    assert q["custom"] is True
    assert client.post("/api/quotes", json={"text": "mine", "author": "me"}).status_code == 409
    first = client.get("/api/quotes").json()[0]
    assert client.delete(f"/api/quotes/{first['id']}").status_code == 403
    assert client.delete(f"/api/quotes/{q['id']}").status_code == 204
    assert client.delete(f"/api/quotes/{q['id']}").status_code == 404
    assert client.post("/api/quotes", json={"text": "   ", "author": "me"}).status_code == 422
    assert client.post("/api/quotes", json={"text": "ok", "author": " \t"}).status_code == 422


def test_lock_log(client):
    assert client.post("/lock-log", json={"app": "Instagram", "extra": 1}).status_code == 204
    assert client.post("/lock-log", data={"app": "TikTok"}).status_code == 204
    assert client.post("/lock-log?app=YouTube").status_code == 204
    assert client.post("/lock-log", content=b"\x00junk", headers={"content-type": "application/json"}).status_code == 204
    assert client.post("/lock-log", json={"app": "x" * 200}).status_code == 204
    assert client.post("/lock-log", json=[1, 2]).status_code == 204
    deep = b"[" * 100000
    assert client.post("/lock-log", content=deep, headers={"content-type": "application/json"}).status_code == 204
    j = client.get("/api/lock-log?limit=10").json()
    apps = [i["app"] for i in j["items"]]
    assert apps == ["unknown", "unknown", "x" * 64, "unknown", "YouTube", "TikTok", "Instagram"]
    assert j["counts_7d"]["Instagram"] == 1 and j["counts_7d"]["unknown"] == 3
    assert j["items"][0]["ts"].endswith("+07:00")


def test_calendar_and_static(client):
    assert client.head("/calendar.ics").status_code == 200
    assert client.get("/lock-log").json()["items"] is not None
    for p in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"):
        assert client.get(p).status_code == 200
    r = client.get("/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "BEGIN:VCALENDAR" in r.text
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["cache-control"] == "no-cache"
    assert client.get("/").status_code == 200
    assert client.get("/api/nope").status_code == 404
