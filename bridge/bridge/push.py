"""Web Push: VAPID key, subscriptions, sending. `_send` is the single network seam."""
import base64
import json
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlparse

import pywebpush
import urllib3
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from . import config

log = logging.getLogger("bridge.push")

# Only these push services may be POSTed to, so a subscription cannot point the bridge at an arbitrary host.
ALLOWED_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")


def allowed_endpoint(endpoint: str) -> bool:
    # urlparse and requests/urllib3 disagree on `https://evil.example\@allowed/x`, so also require that urllib3 sees the same host.
    try:
        u = urlparse(endpoint)
        host = u.hostname or ""
        same = urllib3.util.parse_url(endpoint).host == host
        port = u.port
    except ValueError:  # includes urllib3's LocationParseError
        return False
    return (same and u.scheme == "https" and "\\" not in endpoint and u.username is None and u.password is None
            and port in (None, 443) and any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS))


def ensure_keys() -> None:
    """Create the VAPID private key if missing. Never overwrites, never logs key material."""
    path = config.VAPID_KEY_PATH
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    # Write a temp file, then link it into place: a crash can never leave an empty key at the final path, and link() never overwrites.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        os.link(tmp, path)
    except FileExistsError:
        pass
    finally:
        tmp.unlink(missing_ok=True)


def public_key_b64() -> str:
    try:
        key = serialization.load_pem_private_key(config.VAPID_KEY_PATH.read_bytes(), password=None)
        raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    except Exception:
        log.error("VAPID key unreadable")
        raise
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def moment_payload(day: str) -> dict:
    return {"title": "Time for your Moment", "body": "Post a photo or a short video in the next 30 minutes.",
            "url": "/#/journal", "tag": f"moment-{day}"}


def self_test_payload() -> dict:
    return {"title": "Test notification", "body": "Moment notifications work.", "url": "/#/journal", "tag": "test"}



def _send(sub_info: dict, data: str) -> int:
    """Return the push service's HTTP status. Network errors (requests.RequestException) propagate."""
    try:
        r = pywebpush.webpush(
            subscription_info=sub_info, data=data,
            vapid_private_key=str(config.VAPID_KEY_PATH),
            vapid_claims={"sub": config.settings().VAPID_SUBJECT},  # fresh dict every call: pywebpush writes aud/exp into it
            ttl=3600, headers={"Urgency": "high"}, timeout=10)
    except pywebpush.WebPushException as e:
        # webpush() raises for any status above 202; turn that back into a status code.
        if e.response is None:
            raise
        return e.response.status_code
    return r.status_code


@dataclass
class Results:
    sent: int = 0
    failed: int = 0
    removed: int = 0
    transient: int = 0


def send_all(conn, payload: dict) -> Results:
    # Callers hold no open write transaction, so no write lock is held during the HTTP calls below.
    subs = conn.execute("SELECT id, endpoint, p256dh, auth FROM push_subscriptions ORDER BY id").fetchall()
    data = json.dumps(payload)
    res, outcomes = Results(), []
    for s in subs:
        host = urlparse(s["endpoint"]).hostname
        try:
            status = _send({"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}}, data)
        except Exception as e:
            log.warning("push to %s raised %s", host, type(e).__name__)
            status = None
        if status is not None and 200 <= status < 300:
            res.sent += 1
            outcomes.append(("ok", s["id"]))
        elif status in (404, 410):
            res.removed += 1
            outcomes.append(("gone", s["id"]))
        elif status is None or status == 429 or status >= 500:
            res.transient += 1
            outcomes.append(("fail", s["id"]))
        else:
            res.failed += 1
            outcomes.append(("fail", s["id"]))
        if status is not None and not 200 <= status < 300:
            log.warning("push to %s returned %s", host, status)
    now = config.now().isoformat(timespec="seconds")
    for what, sid in outcomes:
        if what == "ok":
            conn.execute("UPDATE push_subscriptions SET last_ok_at=?, fail_count=0 WHERE id=?", (now, sid))
        elif what == "gone":
            conn.execute("DELETE FROM push_subscriptions WHERE id=?", (sid,))
        else:
            conn.execute("UPDATE push_subscriptions SET fail_count=fail_count+1 WHERE id=?", (sid,))
    conn.commit()
    return res


def subscribe(conn, endpoint: str, p256dh: str, auth: str, user_agent: str | None) -> int:
    conn.execute(
        "INSERT INTO push_subscriptions(endpoint, p256dh, auth, user_agent, created_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(endpoint) DO UPDATE SET p256dh=excluded.p256dh, auth=excluded.auth, "
        "user_agent=excluded.user_agent, fail_count=0",
        (endpoint, p256dh, auth, (user_agent or "")[:200] or None, config.now().isoformat(timespec="seconds")))
    conn.commit()
    return conn.execute("SELECT id FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()["id"]


def unsubscribe(conn, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    conn.commit()
