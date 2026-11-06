"""The plan checker job (UC3): read every pending plan, apply the rules, store the flags.

    uv run python -m sqlibrarian.plan_checker.run --once          # check now
    uv run python -m sqlibrarian.plan_checker.run --nightly       # every night at 02:00

Reads records_db as sqlib_checker (sees every plan, can't write anything) and writes
app_db.plan_flags. Each run replaces the flags of the plans it checked in ONE
transaction, so a crash halfway leaves the previous flags, never half of the new ones.
"""

import argparse
from collections import defaultdict

import psycopg

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import EnrollmentStatus, PlanStatus, TermType
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.plan_checker.rules import Catalog, StudentRecord, check_plan

log = get_logger(__name__)


def load_catalog(cur: psycopg.Cursor) -> Catalog:
    cur.execute("SELECT course_code, credit_hours, min_passed_hours FROM courses")
    rows = cur.fetchall()
    prerequisites = defaultdict(set)
    for code, required in cur.execute(
        "SELECT course_code, required_course_code FROM prerequisites"
    ).fetchall():
        prerequisites[code].add(required)
    rules = cur.execute(
        "SELECT gpa_from, gpa_to, max_credit_hours FROM credit_rules WHERE term_type = %s",
        (TermType.REGULAR,),
    ).fetchall()
    return Catalog(
        hours={c: h for c, h, _ in rows},
        prerequisites=dict(prerequisites),
        min_passed_hours={c: m for c, _, m in rows},
        credit_rules=[(float(a), float(b), h) for a, b, h in rules],
    )


def check_pending(records: psycopg.Connection) -> dict[int, list]:
    """plan_id -> flags, for every pending plan."""
    cur = records.cursor()
    catalog = load_catalog(cur)
    plans = defaultdict(list)
    for plan_id, student_id, course in cur.execute(
        "SELECT p.plan_id, p.student_id, pc.course_code FROM plans p"
        " JOIN plan_courses pc USING (plan_id) WHERE p.status = %s",
        (PlanStatus.PENDING,),
    ).fetchall():
        plans[(plan_id, student_id)].append(course)
    students = {sid for _, sid in plans}
    gpa = dict(cur.execute(
        "SELECT student_id, cumulative_gpa FROM students WHERE student_id = ANY(%s)",
        (list(students),),
    ).fetchall())  # fmt: skip
    passed, now = defaultdict(set), defaultdict(set)
    for sid, code, status in cur.execute(
        "SELECT e.student_id, e.course_code, e.status FROM enrollments e"
        " JOIN terms t USING (term_id) WHERE e.student_id = ANY(%s)"
        " AND (e.status = %s OR (e.status = %s AND t.is_current))",
        (list(students), EnrollmentStatus.PASSED, EnrollmentStatus.IN_PROGRESS),
    ).fetchall():
        (passed if status == EnrollmentStatus.PASSED else now)[sid].add(code)

    results = {}
    for (plan_id, sid), courses in plans.items():
        g = gpa.get(sid)
        record = StudentRecord(
            cumulative_gpa=float(g) if g is not None else None,
            passed=passed[sid],
            in_progress=now[sid],
        )
        results[plan_id] = check_plan(courses, record, catalog)
    return results


def save_flags(app: psycopg.Connection, results: dict[int, list]) -> None:
    with app.transaction(), app.cursor() as cur:
        cur.execute("DELETE FROM plan_flags WHERE plan_id = ANY(%s)", (list(results),))
        cur.executemany(
            "INSERT INTO plan_flags (plan_id, rule, details) VALUES (%s, %s, %s)",
            [(pid, f.rule, f.details) for pid, flags in results.items() for f in flags],
        )


def run_once() -> dict[int, list]:
    s = get_settings()
    with psycopg.connect(s.require("checker_db_url")) as records:
        results = check_pending(records)
    with psycopg.connect(s.require("app_db_url")) as app:
        save_flags(app, results)
    flagged = sum(1 for flags in results.values() if flags)
    log.info("plan_checker.done", pending=len(results), flagged=flagged)
    return results


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--nightly", action="store_true")
    args = p.parse_args()
    if args.once:
        run_once()
        return
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(run_once, "cron", hour=2, minute=0, id="plan_checker")
    log.info("plan_checker.scheduled", at="02:00 every night")
    scheduler.start()


if __name__ == "__main__":
    main()
