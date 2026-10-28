"""Minimal MCP streamable-HTTP client (stdlib) for the YPT study tracker."""
import http.client
import json
import urllib.error
import urllib.request

from . import config

TIMEOUT = 30


class YPTError(Exception):
    pass


def _parse_body(body: str, ctype: str):
    """JSON body or SSE; returns the last JSON-RPC message with a result/error (None if empty)."""
    body = body.strip()
    if not body:
        return None
    if "text/event-stream" in ctype or body.startswith(("event:", "data:", ":")):
        msgs = []
        for line in body.splitlines():
            if line.startswith("data:") and line[5:].strip():
                try:
                    msgs.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    pass
        msgs = [m for m in msgs if isinstance(m, dict) and ("result" in m or "error" in m)]
        return msgs[-1] if msgs else None
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise YPTError(f"unparseable response: {body[:200]!r}") from e


def _post(url, payload, session):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    try:
        req = urllib.request.Request(url, json.dumps(payload).encode(), headers, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return (_parse_body(r.read().decode("utf-8", "replace"), r.headers.get("Content-Type", "")),
                    r.headers.get("Mcp-Session-Id") or session)
    except urllib.error.HTTPError as e:
        raise YPTError(f"HTTP {e.code}: {e.read()[:200]!r}") from e
    except (urllib.error.URLError, OSError) as e:
        raise YPTError(f"request failed: {e}") from e
    except (ValueError, http.client.InvalidURL):
        # The message embeds the URL, which carries the personal key; never let it reach logs or the DB.
        raise YPTError("YPT_URL is malformed") from None


def call_tool(name: str, arguments: dict | None = None):
    url = config.settings().YPT_URL
    if not url:
        raise YPTError("YPT_URL not set")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "boredom-maxxing", "version": "0.1"}}}
    msg, session = _post(url, init, None)
    if not msg or "error" in msg:
        raise YPTError(f"initialize failed: {msg}")
    _post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session)
    call = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}
    msg, _ = _post(url, call, session)
    if not msg:
        raise YPTError("empty tools/call response")
    if "error" in msg:
        raise YPTError(f"tool error: {msg['error']}")
    result = msg.get("result") or {}
    if result.get("isError"):
        raise YPTError(f"tool error: {str(result.get('content'))[:200]}")
    try:
        text = result["content"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise YPTError(f"unexpected result shape: {str(result)[:200]}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def fetch_me_all() -> dict:
    return call_tool("me_all", {})


def _find(obj, keys):
    """Depth-first search for the first int/float value under any of `keys`."""
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), (int, float)) and not isinstance(obj.get(k), bool):
                return obj[k]
        for v in obj.values():
            r = _find(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, keys)
            if r is not None:
                return r
    return None


def _subjects(obj):
    """Find a dict/list of per-subject minutes; shape is a guess, unknown -> {}."""
    if isinstance(obj, dict):
        for k in ("subjects", "subject_minutes", "by_subject", "subject_breakdown"):
            v = obj.get(k)
            if isinstance(v, dict) and v and all(isinstance(x, (int, float)) for x in v.values()):
                return v
            if isinstance(v, list):
                out = {}
                for it in v:
                    if isinstance(it, dict):
                        n = it.get("name") or it.get("subject")
                        m = it.get("minutes", it.get("study_minutes"))
                        if n and isinstance(m, (int, float)):
                            out[str(n)] = m
                if out:
                    return out
        for v in obj.values():
            r = _subjects(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _subjects(v)
            if r:
                return r
    return {}


def store_day(conn, date: str, me_all) -> None:
    summary = me_all.get("study_summary", me_all) if isinstance(me_all, dict) else me_all
    total = _find(summary, ("study_minutes", "total_minutes", "total_study_minutes"))
    subjects = _subjects(summary)
    conn.execute(
        """INSERT INTO ypt_days(date, total_minutes, subjects_json, raw_json, fetched_at) VALUES(?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET total_minutes=excluded.total_minutes,
             subjects_json=excluded.subjects_json, raw_json=excluded.raw_json, fetched_at=excluded.fetched_at""",
        (date, None if total is None else int(total), json.dumps(subjects, ensure_ascii=False),
         json.dumps(me_all, ensure_ascii=False), config.now().isoformat()),
    )
    conn.commit()
