import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from bridge import config, ypt

ME_ALL = {"study_summary": {"yesterday": {"study_minutes": 125,
          "subjects": [{"name": "Math", "minutes": 80}, {"name": "Bio", "minutes": 45}]}}}


def make_server(sse: bool, seen: list):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((msg.get("method"), self.headers.get("Mcp-Session-Id"), self.headers.get("Accept")))
            if "id" not in msg:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if msg["method"] == "initialize":
                res = {"protocolVersion": "2025-06-18", "capabilities": {}}
            else:
                res = {"content": [{"type": "text", "text": json.dumps(ME_ALL)}]}
            rpc = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res})
            body = (f"event: message\ndata: {rpc}\n\n" if sse else rpc).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/json")
            self.send_header("Mcp-Session-Id", "sess-1")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("sse", [True, False])
def test_fetch_me_all(monkeypatch, sse):
    seen = []
    srv = make_server(sse, seen)
    monkeypatch.setenv("YPT_URL", f"http://127.0.0.1:{srv.server_port}/mcp")
    try:
        assert ypt.fetch_me_all() == ME_ALL
    finally:
        srv.shutdown()
    assert [s[0] for s in seen] == ["initialize", "notifications/initialized", "tools/call"]
    assert seen[0][1] is None and seen[1][1] == seen[2][1] == "sess-1"
    assert all(s[2] == "application/json, text/event-stream" for s in seen)


def test_errors(monkeypatch):
    monkeypatch.delenv("YPT_URL", raising=False)
    with pytest.raises(ypt.YPTError):
        ypt.call_tool("me_all")
    monkeypatch.setenv("YPT_URL", "http://127.0.0.1:1/mcp")
    with pytest.raises(ypt.YPTError):
        ypt.call_tool("me_all")


def test_store_day_known_and_unknown(conn):
    ypt.store_day(conn, "2026-10-20", ME_ALL)
    r = conn.execute("SELECT * FROM ypt_days WHERE date='2026-10-20'").fetchone()
    assert r["total_minutes"] == 125
    assert json.loads(r["subjects_json"]) == {"Math": 80, "Bio": 45}
    assert json.loads(r["raw_json"]) == ME_ALL
    ypt.store_day(conn, "2026-10-20", {"weird": "shape"})  # upsert, unknown shape
    r = conn.execute("SELECT * FROM ypt_days WHERE date='2026-10-20'").fetchone()
    assert r["total_minutes"] is None and json.loads(r["raw_json"]) == {"weird": "shape"}
    assert conn.execute("SELECT COUNT(*) FROM ypt_days").fetchone()[0] == 1


def test_malformed_url_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("YPT_URL", "ypt.example/mcp?key=SECRET123")
    with pytest.raises(ypt.YPTError) as e:
        ypt.call_tool("me_all")
    assert "SECRET123" not in str(e.value) and "SECRET123" not in repr(e.value.__cause__)
