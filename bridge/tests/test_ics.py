import re

from bridge import ics


def unfold(text):
    return text.replace("\r\n ", "")


def build(conn, today="2026-10-21"):
    return ics.build_ics(conn, "https://mac.ts.net/", today=today)


def test_structure(conn):
    text = build(conn)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = unfold(text).split("\r\n")[:-1]
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    stack = []
    for l in lines:
        if l.startswith("BEGIN:"):
            stack.append(l[6:])
        elif l.startswith("END:"):
            assert stack.pop() == l[4:]
    assert stack == []
    assert lines.count("BEGIN:VEVENT") == 12 == lines.count("BEGIN:VALARM")
    assert lines.count("BEGIN:VTIMEZONE") == 1 and "TZID:Asia/Jakarta" in lines
    assert lines.count("RRULE:FREQ=WEEKLY") == 12 and lines.count("TRIGGER:-PT30M") == 12
    uids = [l for l in lines if l.startswith("UID:")]
    assert len(set(uids)) == 12 and all(re.fullmatch(r"UID:session-\d+@boredom-maxxing", u) for u in uids)


def test_event_fields(conn):
    lines = unfold(build(conn)).split("\r\n")
    sid = conn.execute("SELECT id FROM sessions WHERE course_code='MAT2111' AND kind='K'").fetchone()[0]
    i = lines.index(f"UID:session-{sid}@boredom-maxxing")
    ev = lines[i:i + 10]
    # 2026-10-21 is a Wednesday, so the Monday class starts 2026-10-26
    assert "DTSTART;TZID=Asia/Jakarta:20261026T100000" in ev
    assert "DTEND;TZID=Asia/Jakarta:20261026T114000" in ev
    assert "SUMMARY:Matematika dan Berpikir Logis (K)" in ev
    assert f"URL:https://mac.ts.net/#/class/{sid}" in ev


def test_today_is_the_weekday_itself(conn):
    assert "DTSTART;TZID=Asia/Jakarta:20261026T100000" in unfold(build(conn, "2026-10-26"))


def test_escape_and_fold(conn):
    conn.execute("UPDATE courses SET name=? WHERE code='MAT2111'", ("A, B; C\\ D " + "é" * 60,))
    conn.execute("UPDATE sessions SET room='Hall, 1;2' WHERE course_code='MAT2111' AND kind='K'")
    text = build(conn)
    for l in text.split("\r\n"):
        assert len(l.encode()) <= 75
    flat = unfold(text)
    assert "SUMMARY:A\\, B\\; C\\\\ D " + "é" * 60 + " (K)" in flat
    assert "LOCATION:Hall\\, 1\\;2" in flat
    assert text.count("\r\n ") > 0  # actually folded
