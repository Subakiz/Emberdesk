import json
import subprocess

import pytest

from bridge import ai


class Fake:
    def __init__(self, result=None, returncode=0, stdout=None, raises=None):
        self.out = stdout if stdout is not None else json.dumps({"result": result, "is_error": False})
        self.returncode, self.raises, self.stderr, self.calls = returncode, raises, "", []

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(cmd, self.returncode, self.out, self.stderr)

    @property
    def stdout(self):
        return self.out


def patch(monkeypatch, fake):
    monkeypatch.setattr(ai.subprocess, "run", fake)
    return fake


def test_flags_and_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(ai.config, "DATA_DIR", tmp_path)
    f = patch(monkeypatch, Fake("hi"))
    assert ai.run_claude("p", allowed_tools=("Read",), add_dirs=(tmp_path,)) == "hi"
    cmd, kw = f.calls[0]
    assert isinstance(cmd, list) and not kw.get("shell")
    assert kw["cwd"] == tmp_path
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    dis = cmd[cmd.index("--disallowedTools") + 1].split(",")
    assert len([d for d in dis if "pluang" in d]) == 9
    assert {"Bash", "Write", "Edit", "WebFetch", "WebSearch"} <= set(dis)
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    assert cmd[cmd.index("--add-dir") + 1] == str(tmp_path)


@pytest.mark.parametrize("fake", [
    Fake(returncode=1, stdout=""),
    Fake(stdout="not json"),
    Fake(stdout=json.dumps({"result": "x", "is_error": True})),
    Fake(raises=subprocess.TimeoutExpired("claude", 1)),
])
def test_errors(monkeypatch, tmp_path, fake):
    monkeypatch.setattr(ai.config, "DATA_DIR", tmp_path)
    patch(monkeypatch, fake)
    with pytest.raises(ai.AIError):
        ai.run_claude("p")


def test_make_tasks_fenced(monkeypatch):
    body = [{"kind": "x", "text": "a"}, {"kind": "y", "text": "b"}, {"kind": "z", "text": "c"}]
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: "Here:\n```json\n" + json.dumps(body) + "\n```")
    t = ai.make_tasks("2026-10-22", "ctx")
    assert [x["kind"] for x in t] == ["class", "physical", "wildcard"]
    assert [x["text"] for x in t] == ["a", "b", "c"]


def test_make_tasks_wrong_count(monkeypatch):
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: '[{"kind":"class","text":"a"}]')
    with pytest.raises(ai.AIError):
        ai.make_tasks("2026-10-22", "")


def test_judge_photo(monkeypatch, tmp_path):
    seen = {}

    def fake(prompt, **kw):
        seen.update(kw)
        return 'Sure {"pass": false, "reason": "screen photo"} done'

    monkeypatch.setattr(ai, "run_claude", fake)
    img = tmp_path / "a.jpg"
    assert ai.judge_photo("t", img) == {"pass": False, "reason": "screen photo"}
    assert seen["allowed_tools"] == ("Read",) and seen["add_dirs"] == (tmp_path,)
    monkeypatch.setattr(ai, "run_claude", lambda *a, **k: '{"pass": "yes"}')
    with pytest.raises(ai.AIError):
        ai.judge_photo("t", img)


def test_brief_and_summary(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(ai, "run_claude", lambda p, **kw: seen.update(kw, prompt=p) or "  ## Key ideas\n- x  ")
    pdf = tmp_path / "s.pdf"
    assert ai.brief("Calc", "K", 3, pdf) == "## Key ideas\n- x"
    assert seen["allowed_tools"] == ("Read",) and seen["add_dirs"] == (tmp_path,) and str(pdf) in seen["prompt"]
    assert ai.summarize_day("2026-10-21", {"total_minutes": 5}, None).startswith("## Key")
