"""Run every reference query in a question file and report errors and results.

    uv run python -m sqlibrarian.evaluation.check_questions data/eval/dev_questions.jsonl
    uv run python -m sqlibrarian.evaluation.check_questions data/eval/dev_questions.jsonl \
        --as-student 238834

--as-student shows what one student would see (imitated with temporary views until
row-level security exists; see evaluation/db.py). Nothing is saved: the whole run
is rolled back.

Exit code 1 if any query fails, so it can run in CI.
"""

import argparse
import json
from pathlib import Path

import psycopg

from sqlibrarian.config import get_settings
from sqlibrarian.evaluation.db import act_as_student


def short(rows: list[tuple], width: int = 90) -> str:
    text = "; ".join(", ".join("NULL" if v is None else str(v) for v in r) for r in rows[:3])
    if len(rows) > 3:
        text += f"; ... (+{len(rows) - 3})"
    return text if len(text) <= width else text[: width - 3] + "..."


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("questions", type=Path)
    p.add_argument("--as-student", type=int, help="show results as this student would see them")
    args = p.parse_args()

    items = [json.loads(x) for x in args.questions.read_text("utf-8").splitlines() if x.strip()]
    failures = 0
    with psycopg.connect(get_settings().records_db_url) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '3s'")
        if args.as_student is not None:
            act_as_student(cur, args.as_student)
        for item in items:
            label = f"{item['id']}  {item['question'][:45]:45s}"
            cur.execute("SAVEPOINT q")
            try:
                cur.execute(item["reference_sql"])
                rows = cur.fetchall()
                print(f"{label}  {len(rows):5d} rows  {short(rows)}")
            except psycopg.Error as e:
                failures += 1
                print(f"{label}  ERROR       {e.diag.message_primary}")
            cur.execute("ROLLBACK TO SAVEPOINT q")
        conn.rollback()

    print(f"\n{len(items)} queries, {failures} failed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
