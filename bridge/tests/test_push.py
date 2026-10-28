import base64
import logging
import stat
from urllib.parse import urlparse

import pytest
import pywebpush
import requests
from py_vapid import Vapid

from bridge import config, push
from journal_helpers import SUB_APPLE, SUB_FCM

REAL_SEND = push._send  # captured before the autouse guard replaces it


def sub_body(endpoint=SUB_APPLE):
    return {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": "BPkey", "auth": "authsecret"}}


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---- keys ----

def test_ensure_keys_creates_0600_and_never_overwrites():
    assert not config.VAPID_KEY_PATH.exists()
    push.ensure_keys()
    assert stat.S_IMODE(config.VAPID_KEY_PATH.stat().st_mode) == 0o600
    first = config.VAPID_KEY_PATH.read_bytes()
    assert first.startswith(b"-----BEGIN PRIVATE KEY-----")
    push.ensure_keys()
    assert config.VAPID_KEY_PATH.read_bytes() == first


def test_ensure_keys_crash_leaves_no_empty_key(monkeypatch):
    def crash(*a):
        raise OSError("crash")

    with monkeypatch.context() as m:
        m.setattr(push.os, "link", crash)
        with pytest.raises(OSError):
            push.ensure_keys()
    assert not config.VAPID_KEY_PATH.exists() and list(config.VAPID_KEY_PATH.parent.glob("vapid*")) == []
    push.ensure_keys()  # the next start recovers
    assert config.VAPID_KEY_PATH.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")


def test_public_key_is_uncompressed_p256_point():
    push.ensure_keys()
    raw = b64d(push.public_key_b64())
    assert len(raw) == 65 and raw[0] == 0x04 and "=" not in push.public_key_b64()


def test_pywebpush_can_use_our_key_file():
    push.ensure_keys()
    claims = {"sub": "mailto:a@b.c", "aud": "https://web.push.apple.com", "exp": 4102444800}
    headers = Vapid.from_file(str(config.VAPID_KEY_PATH)).sign(claims)
    assert headers["Authorization"].lower().startswith("vapid ") and push.public_key_b64() in headers["Authorization"]


def test_unreadable_key_logs_without_content(caplog):
    config.VAPID_KEY_PATH.write_bytes(b"SECRET-NOT-A-KEY")
    with caplog.at_level(logging.DEBUG), pytest.raises(Exception):
        push.public_key_b64()
    assert "VAPID key unreadable" in caplog.text and "SECRET" not in caplog.text


def test_key_never_leaks_into_responses_or_logs(client, caplog):
    pem = config.VAPID_KEY_PATH.read_text()
    body = pem.splitlines()[1]
    with caplog.at_level(logging.DEBUG):
        texts = [client.get("/api/push/public-key").text, client.get("/api/status").text,
                 client.post("/api/push/subscribe", json=sub_body()).text, client.post("/api/push/test").text]
    assert not any(body in t or "PRIVATE KEY" in t for t in texts)
    assert body not in caplog.text and "PRIVATE KEY" not in caplog.text


# ---- API ----

def test_public_key_endpoint(client):
    k = client.get("/api/push/public-key").json()["public_key"]
    assert k == push.public_key_b64() and len(b64d(k)) == 65


def test_subscribe_upsert_is_idempotent(client, conn):
    a = client.post("/api/push/subscribe", json=sub_body(), headers={"user-agent": "UA/1"})
    assert a.status_code == 201
    body = sub_body()
    body["keys"]["auth"] = "newauth"
    b = client.post("/api/push/subscribe", json=body)
    assert b.status_code == 201 and b.json() == a.json()
    rows = conn.execute("SELECT * FROM push_subscriptions").fetchall()
    assert len(rows) == 1 and rows[0]["auth"] == "newauth"
    conn.execute("UPDATE push_subscriptions SET fail_count=3")
    conn.commit()
    client.post("/api/push/subscribe", json=body)
    assert conn.execute("SELECT fail_count FROM push_subscriptions").fetchone()[0] == 0
    assert client.get("/api/status").json()["journal"]["push_subscriptions"] == 1


@pytest.mark.parametrize("endpoint", [
    "https://evil.example.com/push", "http://web.push.apple.com/x", "https://push.apple.com.evil.com/x",
    "https://evil.com/push.apple.com", "https://push.apple.com@evil.com/x", "https://notpush.apple.com/x",
    "ftp://fcm.googleapis.com/x", "nonsense", "https://" + "a" * 1100 + ".push.apple.com/x",
    # requests would send these to evil.example (backslash), or carry credentials / a non-443 port
    "https://evil.example\\@web.push.apple.com/x", "https://127.0.0.1\\@web.push.apple.com/x",
    "https://user:pw@web.push.apple.com/x", "https://web.push.apple.com:8443/x", "https://web.push.apple.com:99999/x"])
def test_subscribe_rejects_bad_endpoints(client, endpoint):
    assert client.post("/api/push/subscribe", json=sub_body(endpoint)).status_code == 422


@pytest.mark.parametrize("endpoint", [SUB_APPLE, SUB_FCM, "https://updates.push.services.mozilla.com/wpush/v2/x",
                                      "https://wns2-par02p.notify.windows.com/x", "https://push.apple.com/x",
                                      "https://web.push.apple.com:443/x"])
def test_subscribe_accepts_allowed_hosts(client, endpoint):
    assert client.post("/api/push/subscribe", json=sub_body(endpoint)).status_code == 201


def test_allowed_endpoint_agrees_with_requests_on_the_host():
    # the bypass case: urlparse says web.push.apple.com, requests would connect to evil.example
    evil = "https://evil.example\\@web.push.apple.com/x"
    assert urlparse(evil).hostname == "web.push.apple.com"
    assert urlparse(requests.Request("POST", evil).prepare().url).hostname == "evil.example"
    assert not push.allowed_endpoint(evil)
    assert push.allowed_endpoint(SUB_APPLE) and not push.allowed_endpoint("")


def test_subscribe_validates_keys(client):
    bad = sub_body()
    bad["keys"]["p256dh"] = "x" * 201
    assert client.post("/api/push/subscribe", json=bad).status_code == 422
    assert client.post("/api/push/subscribe", json={"endpoint": SUB_APPLE}).status_code == 422


def test_unsubscribe(client, conn):
    client.post("/api/push/subscribe", json=sub_body())
    assert client.post("/api/push/unsubscribe", json={"endpoint": SUB_APPLE}).status_code == 204
    assert conn.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0] == 0
    assert client.post("/api/push/unsubscribe", json={"endpoint": "https://nope.push.apple.com/z"}).status_code == 204
    assert client.post("/api/push/unsubscribe", json={}).status_code == 422


def test_test_endpoint_counts_and_isolates_failures(client, conn, monkeypatch):
    for ep in (SUB_APPLE, SUB_FCM, "https://a.push.apple.com/1", "https://b.push.apple.com/2"):
        client.post("/api/push/subscribe", json=sub_body(ep))
    plan = {SUB_APPLE: requests.ConnectionError("down"), SUB_FCM: 201, "https://a.push.apple.com/1": 410, "https://b.push.apple.com/2": 403}
    seen = []

    def send(sub, data):
        seen.append((sub["endpoint"], data))
        r = plan[sub["endpoint"]]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(push, "_send", send)
    r = client.post("/api/push/test")
    assert r.status_code == 200 and r.json() == {"sent": 1, "failed": 2, "removed": 1}
    assert len(seen) == 4  # the raising subscription did not stop the rest
    assert '"tag": "test"' in seen[0][1] and sub_body()["keys"]["auth"] not in seen[0][1]
    rows = {r["endpoint"]: r for r in conn.execute("SELECT * FROM push_subscriptions")}
    assert "https://a.push.apple.com/1" not in rows
    assert rows[SUB_APPLE]["fail_count"] == 1 and rows[SUB_FCM]["fail_count"] == 0 and rows[SUB_FCM]["last_ok_at"]
    assert rows["https://b.push.apple.com/2"]["fail_count"] == 1


def test_send_all_result_buckets(conn, monkeypatch):
    for i, ep in enumerate(["https://a.push.apple.com/1", "https://b.push.apple.com/2", "https://c.push.apple.com/3",
                            "https://d.push.apple.com/4", "https://e.push.apple.com/5", "https://f.push.apple.com/6"]):
        push.subscribe(conn, ep, "k", "a", None)
    codes = iter([201, 202, 404, 429, 503, 413])
    monkeypatch.setattr(push, "_send", lambda s, d: next(codes))
    r = push.send_all(conn, {"title": "x"})
    assert (r.sent, r.removed, r.transient, r.failed) == (2, 1, 2, 1) and r.sent + r.removed + r.transient + r.failed == 6


# ---- the pywebpush seam ----

class Resp:
    def __init__(self, code):
        self.status_code = code


def test_send_maps_exception_to_status(monkeypatch):
    def boom(**kw):
        raise pywebpush.WebPushException("Push failed", response=Resp(410))

    monkeypatch.setattr(pywebpush, "webpush", boom)
    assert REAL_SEND({"endpoint": SUB_APPLE, "keys": {}}, "{}") == 410


def test_send_returns_success_status_and_fresh_claims(monkeypatch):
    claims = []

    def ok(**kw):
        claims.append(kw["vapid_claims"])
        kw["vapid_claims"]["aud"] = "https://x"  # what pywebpush does to the dict
        assert kw["vapid_private_key"] == str(config.VAPID_KEY_PATH) and kw["timeout"] == 10 and kw["ttl"] == 3600
        return Resp(201)

    monkeypatch.setattr(pywebpush, "webpush", ok)
    assert REAL_SEND({"endpoint": SUB_APPLE, "keys": {}}, "{}") == 201
    assert REAL_SEND({"endpoint": SUB_APPLE, "keys": {}}, "{}") == 201
    assert claims[0] is not claims[1] and claims[1].get("aud") == "https://x" and set(claims[0]) >= {"sub"}


def test_send_without_response_propagates(monkeypatch):
    def boom(**kw):
        raise pywebpush.WebPushException("bad key")

    monkeypatch.setattr(pywebpush, "webpush", boom)
    with pytest.raises(pywebpush.WebPushException):
        REAL_SEND({"endpoint": SUB_APPLE, "keys": {}}, "{}")
