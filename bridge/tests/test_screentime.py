import json
import sqlite3
import subprocess
from datetime import datetime

import pytest

from bridge import ai, config, screentime


@pytest.fixture
def archive(tmp_path, monkeypatch):
    path = tmp_path / "archive.sqlite"
    monkeypatch.setattr(config, "screen_time_archive", lambda: path)
    monkeypatch.setattr(config, "now", lambda: datetime(2026, 10, 26, 12, tzinfo=config.TZ))
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE sessions(device_id TEXT,bundle_id TEXT,start_ts REAL,end_ts REAL)")
    return path


def session(path, app, start, end, device="iphone"):
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (device, app,
                  datetime.fromisoformat(start).timestamp(), datetime.fromisoformat(end).timestamp()))


def test_midnight_overlap_idempotency_and_filter(conn, archive):
    session(archive, "com.zhiliaoapp.musically", "2026-10-25T23:50:00+07:00", "2026-10-26T00:10:00+07:00")
    session(archive, "com.zhiliaoapp.musically", "2026-10-26T00:00:00+07:00", "2026-10-26T00:20:00+07:00", "mac")
    session(archive, "com.burbn.instagram", "2026-10-26T01:00:00+07:00", "2026-10-26T01:30:00+07:00")
    session(archive, "com.example.other", "2026-10-26T02:00:00+07:00", "2026-10-26T05:00:00+07:00")
    screentime.import_archive(conn)
    fingerprint = screentime.summary(conn, 7)["fingerprint"]
    screentime.import_archive(conn)
    result = screentime.summary(conn, 7)
    assert result["total_seconds"] == 3600
    assert result["tiktok_seconds"] == 1800 and result["instagram_seconds"] == 1800
    assert result["history"][0]["tiktok_seconds"] == 1200
    assert result["fingerprint"] == fingerprint
    assert conn.execute("SELECT count(*) FROM screen_time_days").fetchone()[0] == 2


def test_manual_overrides_import_and_can_be_removed(client, conn, archive):
    session(archive, "com.burbn.instagram", "2026-10-26T01:00:00+07:00", "2026-10-26T01:30:00+07:00")
    screentime.import_archive(conn)
    assert client.put("/api/screen-time/2026-10-26", json={"tiktok_minutes": 10, "instagram_minutes": 5}).status_code == 200
    screentime.import_archive(conn)
    assert client.get("/api/screen-time?days=1").json()["total_seconds"] == 900
    assert client.get("/api/screen-time/2026-10-26").json()["source"] == "manual"
    assert client.delete("/api/screen-time/2026-10-26").status_code == 204
    assert client.get("/api/screen-time?days=1").json()["total_seconds"] == 1800


@pytest.mark.parametrize("body", [
    {"tiktok_minutes": -1, "instagram_minutes": 0}, {"tiktok_minutes": 1441, "instagram_minutes": 0},
    {"tiktok_minutes": 1.5, "instagram_minutes": 0}, {"tiktok_minutes": True, "instagram_minutes": 0},
    {"tiktok_minutes": 1},
])
def test_manual_validation(client, archive, body):
    assert client.put("/api/screen-time/2026-10-26", json=body).status_code == 422


def test_date_and_period_validation(client, archive):
    assert client.put("/api/screen-time/2026-10-27", json={"tiktok_minutes": 0, "instagram_minutes": 0}).status_code == 422
    assert client.get("/api/screen-time/nonsense").status_code == 422
    assert client.get("/api/screen-time?days=8").status_code == 422
    assert client.post("/api/screen-time/ideas?days=8").status_code == 422
    assert client.get("/api/screen-time/2026-10-20").json()["tiktok_seconds"] is None


def test_import_failure_preserves_totals_and_sync_time(client, conn, archive, tmp_path, monkeypatch):
    session(archive, "com.burbn.instagram", "2026-10-26T01:00:00+07:00", "2026-10-26T01:30:00+07:00")
    screentime.import_archive(conn)
    binary = tmp_path / "reader"
    binary.touch()
    monkeypatch.setattr(config, "screen_time_binary", lambda: binary)
    monkeypatch.setattr(screentime.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(
        cmd, 0, "" if cmd[1] == "kickstart" else "state = not running\nlast exit code = 1", ""))
    result = client.post("/api/screen-time/sync").json()
    assert "Full Disk Access" in result["error"] and result["last_synced"]
    assert client.get("/api/screen-time").json()["total_seconds"] == 1800


def test_sync_uses_background_job_and_imports_when_it_exits(client, conn, archive, tmp_path, monkeypatch):
    session(archive, "com.burbn.instagram", "2026-10-26T01:00:00+07:00", "2026-10-26T01:30:00+07:00")
    binary = tmp_path / "reader"
    binary.touch()
    monkeypatch.setattr(config, "screen_time_binary", lambda: binary)
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "" if cmd[1] == "kickstart" else "state = not running\nlast exit code = 0", "")
    monkeypatch.setattr(screentime.subprocess, "run", run)
    assert client.post("/api/screen-time/sync").json()["error"] is None
    assert [cmd[1] for cmd in calls] == ["kickstart", "print"]
    assert client.get("/api/screen-time").json()["total_seconds"] == 1800


def test_missing_background_job_is_actionable(client, tmp_path, monkeypatch):
    binary = tmp_path / "reader"
    binary.touch()
    monkeypatch.setattr(config, "screen_time_binary", lambda: binary)
    monkeypatch.setattr(screentime.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 3, "", ""))
    assert "setup script" in client.post("/api/screen-time/sync").json()["error"]


def test_missing_reader(client, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "screen_time_binary", lambda: tmp_path / "missing")
    assert "not installed" in client.post("/api/screen-time/sync").json()["error"]


def ideas(minutes=10):
    return [{"kind": k, "title": f"Specific {k}", "minutes": minutes,
             "outcome": "An estimated beginner milestone", "steps": ["Get materials", "Practice the first step"]}
            for k in ("skill", "build", "study")]


def test_ideas_cached_and_invalidated_by_budget(client, conn, archive, monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "time_equivalents", lambda m, c: calls.append(m) or ideas(m))
    assert client.post("/api/screen-time/ideas").status_code == 422
    client.put("/api/screen-time/2026-10-26", json={"tiktok_minutes": 15, "instagram_minutes": 5})
    first = client.post("/api/screen-time/ideas").json()
    assert len(first["ideas"]) == 3 and calls == [20]
    assert client.post("/api/screen-time/ideas").json()["ideas"] == first["ideas"]
    client.put("/api/screen-time/2026-10-26", json={"tiktok_minutes": 15, "instagram_minutes": 5})
    client.post("/api/screen-time/ideas")
    assert calls == [20]
    client.put("/api/screen-time/2026-10-26", json={"tiktok_minutes": 20, "instagram_minutes": 5})
    assert client.get("/api/screen-time").json()["ideas"] == []
    client.post("/api/screen-time/ideas")
    assert calls == [20, 25]


def test_ai_validates_budget_and_shape(monkeypatch):
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: json.dumps(ideas()))
    assert len(ai.time_equivalents(10, ["Calculus"])) == 3
    with pytest.raises(ai.AIError):
        ai.time_equivalents(9, [])
    invalid = ideas()
    invalid[0]["minutes"] = True
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: json.dumps(invalid))
    with pytest.raises(ai.AIError):
        ai.time_equivalents(10, [])


def test_ai_normalizes_order_without_discarding_valid_ideas(monkeypatch):
    response = list(reversed(ideas()))
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: json.dumps(response))
    assert [i["kind"] for i in ai.time_equivalents(10, [])] == ["skill", "build", "study"]


def test_ai_error_keeps_logs(client, archive, monkeypatch):
    client.put("/api/screen-time/2026-10-26", json={"tiktok_minutes": 20, "instagram_minutes": 5})
    def fail(*args):
        raise ai.AIError("unavailable")
    monkeypatch.setattr(ai, "time_equivalents", fail)
    assert client.post("/api/screen-time/ideas").status_code == 503
    assert client.get("/api/screen-time").json()["total_seconds"] == 1500
