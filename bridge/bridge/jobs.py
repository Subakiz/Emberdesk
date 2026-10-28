"""Evening job: brief tomorrow's classes, make tomorrow's tasks, pull YPT, summarize yesterday."""
import argparse
import json
from datetime import datetime, timedelta

from . import ai, config, db, ypt


def _shift(date: str, days: int) -> str:
    return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def brief_occurrence(conn, occ_id: int) -> bool:
    from . import materials, occurrences

    occ = occurrences.get(conn, occ_id)
    if not occ or occ["status"] == "cancelled" or occ["briefing_md"]:
        return False
    files = materials.for_course(conn, occ["course_code"])
    week = occ["week"] or occurrences.next_unbriefed_week(conn, occ["course_code"], occ["kind"])
    pdf = files.get((occ["kind"], week)) if week is not None else None
    if pdf is None:
        return False
    md = ai.brief(occ["course_name"], occ["kind"], week, pdf)
    # Week is only stored on success so a failed AI call does not burn it.
    conn.execute("UPDATE occurrences SET week=?, briefing_md=?, briefed_at=? WHERE id=?",
                 (week, md, config.now().isoformat(), occ_id))
    conn.commit()
    return True


def ensure_tasks(conn, date: str) -> bool:
    from . import occurrences

    have = conn.execute("SELECT COUNT(*) FROM tasks WHERE date=?", (date,)).fetchone()[0]
    if have >= 3:
        return False
    classes = [o for o in occurrences.list_day(conn, date) if o["status"] == "scheduled"]
    if classes:
        ctx = "\n".join(f"- {o['course_name']} ({o['kind']}) {o['start']}-{o['end']}"
                        + (f", week {o['week']}" if o["week"] else "") for o in classes)
    else:
        names = [r["name"] for r in conn.execute("SELECT name FROM courses ORDER BY code")]
        ctx = "No classes that day. Courses the student takes: " + ", ".join(names)
    tasks = ai.make_tasks(date, ctx)
    for slot, t in enumerate(tasks, 1):
        conn.execute("INSERT OR IGNORE INTO tasks(date, slot, kind, text) VALUES(?,?,?,?)",
                     (date, slot, t["kind"], t["text"]))
    conn.commit()
    return True


def fetch_ypt(conn) -> bool:
    if not config.settings().YPT_URL:
        print("ypt: YPT_URL not set, skipping")
        return False
    # Free YPT accounts only ever return yesterday.
    ypt.store_day(conn, _shift(config.today(), -1), ypt.fetch_me_all())
    return True


def summarize(conn, date: str) -> bool:
    if conn.execute("SELECT 1 FROM summaries WHERE date=?", (date,)).fetchone():
        return False
    y = conn.execute("SELECT * FROM ypt_days WHERE date=?", (date,)).fetchone()
    n = conn.execute("SELECT text FROM notes WHERE date=?", (date,)).fetchone()
    if not y and not n:
        return False
    ypt_day = None
    if y:
        ypt_day = {"total_minutes": y["total_minutes"], "subjects": json.loads(y["subjects_json"] or "{}")}
    text = ai.summarize_day(date, ypt_day, n["text"] if n else None)
    conn.execute("INSERT OR REPLACE INTO summaries(date, text) VALUES(?,?)", (date, text))
    conn.commit()
    return True


def evening(target_date: str | None = None) -> dict:
    from . import occurrences

    target = target_date or _shift(config.today(), 1)
    conn = db.connect()
    try:
        db.init_db(conn)
        run = conn.execute("INSERT INTO job_runs(job, started_at) VALUES('evening', ?)",
                           (config.now().isoformat(),)).lastrowid
        conn.commit()
        results, log, ok = {}, [], True

        def step(name, fn, *args):
            nonlocal ok
            try:
                results[name] = fn(*args)
                log.append(f"{name}: {results[name]}")
            except Exception as e:  # one failing step must not stop the rest
                ok = False
                results[name] = f"error: {e}"
                log.append(f"{name}: ERROR {type(e).__name__}: {e}")

        step("materialize", occurrences.materialize, conn, target)
        try:
            ids = [o["id"] for o in occurrences.list_day(conn, target)
                   if o["status"] == "scheduled" and not o["briefing_md"]]
        except Exception as e:
            ok = False
            ids = []
            log.append(f"list_day: ERROR {e}")
        for i in ids:
            step(f"brief:{i}", brief_occurrence, conn, i)
        step("tasks", ensure_tasks, conn, target)
        step("ypt", fetch_ypt, conn)
        step("summary", summarize, conn, _shift(config.today(), -1))

        conn.execute("UPDATE job_runs SET finished_at=?, ok=?, log=? WHERE id=?",
                     (config.now().isoformat(), int(ok), "\n".join(log), run))
        conn.commit()
        print("\n".join(log))
        return results
    finally:
        conn.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="bridge.jobs")
    sub = p.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("evening")
    e.add_argument("--date", help="target date YYYY-MM-DD (default: tomorrow)")
    args = p.parse_args(argv)
    if args.date:
        datetime.strptime(args.date, "%Y-%m-%d")
    evening(args.date)


if __name__ == "__main__":
    main()
