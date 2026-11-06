"""Step 2.2 (4-5): keep only pairs whose SQL is safe, runs, and returns rows;
then remove duplicates.

Checks, in order (each rejection is counted so you can see where pairs die):
  1. the SQL is exactly one SELECT
  2. every course it names exists, and it doesn't filter by student
  3. it runs on records_db within the statement timeout, read-only
  4. it returns rows (empty is allowed only for "not recorded yet" questions)
  5. its normalized form hasn't been seen before

Run (laptop, records_db up, RECORDS_DB_URL in .env):
    uv run python -m sqlibrarian.training.validate_pairs
"""

import argparse
import json
from collections import Counter
from pathlib import Path

import psycopg

from sqlibrarian.config import get_settings
from sqlibrarian.enums import Lang
from sqlibrarian.log import configure_logging, get_logger
from sqlibrarian.training.sql_utils import (
    course_names_used,
    filters_by_student,
    is_single_select,
    normalize_sql,
)

log = get_logger(__name__)


def run_readonly(conn: psycopg.Connection, sql: str, timeout: str) -> int | None:
    """Run the query in a read-only transaction with a time limit.
    Returns the number of rows, or None if it failed. Nothing is ever saved."""
    with conn.cursor() as cur:
        try:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
            cur.execute(sql)
            return len(cur.fetchall())
        except psycopg.Error:
            return None
        finally:
            cur.execute("ROLLBACK")


def batch_lang(batch: dict) -> Lang:
    """Lines written before the "lang" field existed only have the prompt's wording."""
    if "lang" in batch:
        return Lang(batch["lang"])
    return Lang.AR if batch["language"].startswith("Modern") else Lang.EN


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--raw", type=Path, default=s.paths.raw_generated)
    p.add_argument("--out", type=Path, default=s.paths.own_pairs)
    args = p.parse_args()

    kept, seen, reasons = [], set(), Counter()
    with psycopg.connect(s.records_dsn, autocommit=True) as conn:
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
                if course_names_used(sql) - known_courses:
                    reasons["unknown course name"] += 1
                    continue
                if filters_by_student(sql):
                    reasons["filters by student"] += 1
                    continue
                n_rows = run_readonly(conn, sql, s.statement_timeout)
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
                        "lang": batch_lang(batch),
                        "rows": n_rows,
                    }
                )

    with args.out.open("w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "validate.done", kept=len(kept), total=len(kept) + sum(reasons.values()),
        out=str(args.out), dropped=dict(reasons.most_common()),
    )  # fmt: skip
    log.info(
        "validate.by_type", **{q[:40]: n for q, n in Counter(r["qtype"] for r in kept).items()}
    )
    log.info("validate.by_lang", **Counter(r["lang"] for r in kept))


if __name__ == "__main__":
    main()
