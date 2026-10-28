from datetime import date as Date, datetime, timedelta, timezone

from . import config

_VTIMEZONE = [
    "BEGIN:VTIMEZONE",
    "TZID:Asia/Jakarta",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0700",
    "TZOFFSETTO:+0700",
    "TZNAME:WIB",
    "END:STANDARD",
    "END:VTIMEZONE",
]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode())
        # continuation lines start with a space that counts toward the 75 octets
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


def _local(d: Date, hhmm: str) -> str:
    return f"{d:%Y%m%d}T{hhmm.replace(':', '')}00"


def build_ics(conn, public_url: str, today=None) -> str:
    if today is None:
        today = config.today()
    d0 = Date.fromisoformat(today) if isinstance(today, str) else today
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Boredom-Maxxing//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:Boredom-Maxxing", "X-WR-TIMEZONE:Asia/Jakarta", *_VTIMEZONE]
    rows = conn.execute(
        """SELECT s.*, c.name FROM sessions s JOIN courses c ON c.code=s.course_code ORDER BY s.id""")
    for s in rows:
        first = d0 + timedelta(days=(s["weekday"] - d0.weekday()) % 7)
        url = f"{public_url.rstrip('/')}/#/class/{s['id']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:session-{s['id']}@boredom-maxxing",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=Asia/Jakarta:{_local(first, s['start'])}",
            f"DTEND;TZID=Asia/Jakarta:{_local(first, s['end'])}",
            "RRULE:FREQ=WEEKLY",
            f"SUMMARY:{_esc(f'{s['name']} ({s['kind']})')}",
        ]
        if s["room"]:
            lines.append(f"LOCATION:{_esc(s['room'])}")
        lines += [
            f"URL:{url}",
            f"DESCRIPTION:{_esc(url)}",
            "BEGIN:VALARM",
            "TRIGGER:-PT30M",
            "ACTION:DISPLAY",
            "DESCRIPTION:Class soon",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(_fold(l) + "\r\n" for l in lines)
