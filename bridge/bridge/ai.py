"""The only module that spawns `claude`. Every call is sandboxed: no hooks/MCP/session, no Pluang orders."""
import json
import subprocess
from pathlib import Path

from . import config

MODEL = "claude-haiku-5-5"
_PLUANG = [
    f"mcp__claude_ai_pluang__{m}_{a}"
    for m in ("us_stocks", "crypto", "indo_stocks")
    for a in ("place_buy_order", "place_sell_order", "cancel_order")
]
DISALLOWED = _PLUANG + ["Bash", "Write", "Edit", "WebFetch", "WebSearch"]


class AIError(Exception):
    pass


def run_claude(prompt: str, *, allowed_tools=(), add_dirs=(), timeout=180) -> str:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        config.settings().CLAUDE_BIN, "-p", prompt,
        "--model", MODEL, "--effort", "high", "--output-format", "json",
        "--safe-mode", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-session-persistence",
        "--disallowedTools", ",".join(DISALLOWED),
    ]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    for d in add_dirs:
        cmd += ["--add-dir", str(d)]
    try:
        p = subprocess.run(cmd, cwd=config.DATA_DIR, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        raise AIError(f"claude timed out after {timeout}s") from e
    except OSError as e:
        raise AIError(f"cannot run claude: {e}") from e
    if p.returncode != 0:
        # --output-format json reports errors like "Not logged in" in `result`, not stderr.
        try:
            msg = json.loads(p.stdout).get("result")
        except (json.JSONDecodeError, AttributeError):
            msg = None
        raise AIError(f"claude exit {p.returncode}: {msg or (p.stderr or p.stdout).strip()[:300]}")
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise AIError(f"unparseable claude output: {p.stdout[:200]!r}") from e
    if not isinstance(out, dict) or out.get("is_error") or not isinstance(out.get("result"), str):
        raise AIError(f"claude error: {str(out)[:300]}")
    return out["result"]


def _extract_json(text: str, opener: str):
    """First balanced JSON value starting at `opener` ('{' or '['); tolerates fences/prose."""
    text = text.replace("```json", "```")
    dec = json.JSONDecoder()
    i = text.find(opener)
    while i != -1:
        try:
            return dec.raw_decode(text[i:])[0]
        except json.JSONDecodeError:
            i = text.find(opener, i + 1)
    raise AIError(f"no JSON {opener} in model output: {text[:200]!r}")


def brief(course_name: str, kind: str, week: int, pdf_path) -> str:
    pdf = Path(pdf_path)
    label = "lecture (Kuliah)" if kind == "K" else "tutorial (Responsi)"
    prompt = (
        f"Read the PDF slides at {pdf} ({course_name}, {label}, week {week}). "
        "The slides are probably Indonesian; answer in English unless the material demands otherwise. "
        "Write a concise markdown briefing for a student to read before class, with exactly these sections: "
        "'## Key ideas' (3-6 bullets), '## Terms' (short glossary bullets), "
        "'## Think about before class' (3 questions). Under 250 words. Output only the markdown."
    )
    return run_claude(prompt, allowed_tools=("Read",), add_dirs=(pdf.parent,)).strip()


def make_tasks(date: str, classes_ctx: str) -> list[dict]:
    prompt = (
        f"Create 3 small daily challenges for a university student for {date}. Context about that day's classes:\n"
        f"{classes_ctx or '(no classes that day)'}\n\n"
        "Return ONLY a strict JSON array of exactly 3 objects {\"kind\": ..., \"text\": ...}, in this order: "
        "kind 'class' (tied to a class/course topic above, or a short study task if no classes), "
        "kind 'physical' (real-world, away from screens), kind 'wildcard' (creative or curious). "
        "Each must be doable within one day and provable by ONE photo. text: one imperative sentence, "
        "say what the photo must show."
    )
    data = _extract_json(run_claude(prompt), "[")
    if not isinstance(data, list) or len(data) != 3:
        raise AIError(f"expected 3 tasks, got: {str(data)[:200]}")
    kinds = ("class", "physical", "wildcard")
    tasks = []
    for want, t in zip(kinds, data):
        text = str(t.get("text", "")).strip() if isinstance(t, dict) else ""
        if not text:
            raise AIError(f"bad task: {t!r}")
        tasks.append({"kind": want, "text": text})
    return tasks


def judge_photo(task_text: str, image_path) -> dict:
    img = Path(image_path)
    prompt = (
        f"Read the image file at {img}. It is a student's photo proof for this task: {task_text!r}.\n"
        "Judge strictly whether the photo genuinely shows the task done. FAIL photos of screens or monitors, "
        "stock/internet images, and anything unrelated or too unclear to verify. "
        'Return ONLY strict JSON: {"pass": true|false, "reason": "one short line"}.'
    )
    data = _extract_json(run_claude(prompt, allowed_tools=("Read",), add_dirs=(img.parent,)), "{")
    if not isinstance(data, dict) or not isinstance(data.get("pass"), bool):
        raise AIError(f"bad verdict: {str(data)[:200]}")
    return {"pass": data["pass"], "reason": str(data.get("reason", "")).strip().split("\n")[0]}


def summarize_day(date: str, ypt_day: dict | None, note: str | None) -> str:
    prompt = (
        f"Write a 2-3 sentence plain-text summary of a student's study day, {date}. "
        f"Study tracker data (may be empty): {json.dumps(ypt_day, ensure_ascii=False) if ypt_day else 'none'}\n"
        f"Their own note on what they learned: {note or 'none'}\n"
        "Be factual and warm, mention minutes and subjects if known, no invented details. Output only the summary."
    )
    return run_claude(prompt).strip()


def time_equivalents(minutes: int, courses: list[str]) -> list[dict]:
    prompt = (
        f"A university student spent {minutes} minutes combined on TikTok and Instagram. "
        "Suggest 3 alternative uses of that SAME time budget, not three things to do together. "
        f"Their courses: {json.dumps(courses)}. "
        "Return ONLY a JSON array with exactly 3 objects in this order: kind skill, build, study. "
        'Each object: {"kind":...,"title":...,"minutes":integer,"outcome":...,"steps":[...]}. '
        "Choose specific interesting goals: e.g. a bicycle wheelie practice milestone, a working expense tracker, "
        "or learning a named topic in a course. Each minutes value must be 1 to the budget inclusive. "
        "All steps for each idea must fit its minutes. Give 2-4 concrete steps and a tangible realistic outcome. "
        "For large budgets spread practice across sessions. State prerequisites/equipment in the steps. "
        "For physical skills include safe practice conditions and protective equipment. "
        "Do not guarantee mastery, exaggerate what a beginner can accomplish, shame the user, or invent research. "
        "Describe outcomes as estimates. Keep titles under 90 characters, outcome under 500, steps under 300 each."
    )
    data = _extract_json(run_claude(prompt), "[")
    if not isinstance(data, list) or len(data) != 3:
        raise AIError("Expected three time-equivalent ideas. Try again.")
    kinds = ("skill", "build", "study")
    if any(not isinstance(item, dict) or item.get("kind") not in kinds for item in data):
        raise AIError("Expected a skill, build, and study idea. Try again.")
    if len({item["kind"] for item in data}) != 3:
        raise AIError("Expected three different kinds of ideas. Try again.")
    data.sort(key=lambda item: kinds.index(item["kind"]))
    for item in data:
        if (type(item.get("minutes")) is not int or not 1 <= item["minutes"] <= minutes
                or not all(isinstance(item.get(k), str) and 0 < len(item[k].strip()) <= limit
                           for k, limit in (("title", 160), ("outcome", 1000)))
                or not isinstance(item.get("steps"), list) or not 2 <= len(item["steps"]) <= 4
                or not all(isinstance(s, str) and 0 < len(s.strip()) <= 1000 for s in item["steps"])):
            raise AIError("The generated ideas did not fit your time budget. Try again.")
    return [{k: item[k] for k in ("kind", "title", "minutes", "outcome", "steps")} for item in data]
