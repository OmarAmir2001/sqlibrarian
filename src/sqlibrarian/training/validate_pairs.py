"""Step 2.2 (4-5): keep only pairs whose SQL is safe, runs, and returns rows;
then remove duplicates.

Checks, in order (each rejection is counted so you can see where pairs die):
  1. the SQL is exactly one SELECT
  2. every course it names exists, and it doesn't filter by student
  3. it runs on records_db within 3 seconds, read-only
  4. it returns rows (empty is allowed only for "not recorded yet" questions)
  5. its normalized form hasn't been seen before

Run (laptop, records_db up, RECORDS_DB_URL in .env, e.g.
postgresql://user:pass@localhost:5433/records_db):
    uv run python -m sqlibrarian.training.validate_pairs
"""

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from sqlibrarian.training.sql_utils import (
    course_names_used,
    filters_by_student,
    is_single_select,
    normalize_sql,
)


def run_readonly(conn: psycopg.Connection, sql: str) -> int | None:
    """Run the query in a read-only transaction with a 3 s limit.
    Returns the number of rows, or None if it failed. Nothing is ever saved."""
    with conn.cursor() as cur:
        try:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '3s'")
            cur.execute(sql)
            return len(cur.fetchall())
        except psycopg.Error:
            return None
        finally:
            cur.execute("ROLLBACK")


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=Path("data/training/raw_generated.jsonl"))
    p.add_argument("--out", type=Path, default=Path("data/training/own_pairs.jsonl"))
    args = p.parse_args()

    kept, seen, reasons = [], set(), Counter()
    with psycopg.connect(os.environ["RECORDS_DB_URL"], autocommit=True) as conn:
        titles = conn.execute("SELECT title_en, title_ar FROM courses").fetchall()
        known_courses = {t for row in titles for t in row}
        for line in args.raw.read_text(encoding="utf-8").splitlines():
            batch = json.loads(line)
            for pair in batch["pairs"]:
                question = (pair.get("question") or "").strip()
                sql = (pair.get("sql") or "").strip().rstrip(";")
                if not question or not sql:
                    reasons["empty"] += 1
                    continue
                if not is_single_select(sql):
                    reasons["not a single SELECT"] += 1
                    continue
                unknown = course_names_used(sql) - known_courses
                if unknown:
                    reasons["unknown course name"] += 1
                    continue
                if filters_by_student(sql):
                    reasons["filters by student"] += 1
                    continue
                n_rows = run_readonly(conn, sql)
                if n_rows is None:
                    reasons["failed to run"] += 1
                    continue
                if n_rows == 0 and "not recorded" not in batch["qtype"]:
                    reasons["no rows"] += 1
                    continue
                key = normalize_sql(sql)
                if key in seen:
                    reasons["duplicate"] += 1
                    continue
                seen.add(key)
                kept.append(
                    {
                        "id": f"own-{len(kept):05d}",
                        "question": question,
                        "reference_sql": sql,
                        "qtype": batch["qtype"],
                        "difficulty": batch["difficulty"],
                        "lang": "ar" if batch["language"].startswith("Modern") else "en",
                        "rows": n_rows,
                    }
                )

    with args.out.open("w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    total = len(kept) + sum(reasons.values())
    print(f"kept {len(kept)} of {total} pairs")
    for reason, n in reasons.most_common():
        print(f"  dropped {n:5d}  {reason}")
    print("by type:", Counter(r["qtype"] for r in kept).most_common())


if __name__ == "__main__":
    main()
