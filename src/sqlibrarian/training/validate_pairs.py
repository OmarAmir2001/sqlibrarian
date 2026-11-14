"""Step 2.2 (4-5): keep only pairs whose SQL is safe, runs, and returns rows;
then remove duplicates.

Checks, in order (each rejection is counted so you can see where pairs die):
  1. the SQL is exactly one SELECT
  2. every course it names exists, it doesn't filter by student, and it doesn't find
     "last term" by counting from the newest term (sql_utils.picks_newest_term)
  3. it runs on records_db within the statement timeout, read-only
  4. it returns rows (empty is allowed only for "not recorded yet" questions)
  5. it tells someone something: not only NULLs for the whole database, or a value for at
     least 2 of ~30 single students (the scorer's sample + others spread across all). This
     catches "last term" written as the newest term (no grades yet): one NULL row, which
     check 4 alone accepts
  6. it isn't a repeat: the same question with the same SQL, or more than
     `max_phrasings_per_sql` questions in one language for one SQL. Different wordings of
     one query are kept on purpose ("what's my GPA" has many); the first keeps the plain id,
     the others get -2, -3... and share its group, so they never sit on both sides of a split

Arabic, Egyptian and mixed pairs show Arabic names: a shown title_en / name_en becomes
title_ar / name_ar (sql_utils.arabic_output), kept only if the query still returns the
same number of rows.

Each kept pair gets an id made from its normalized SQL, so the id stays the same when
the database or the raw file changes. The Egyptian/mixed rewrites (rewrite_arabic.py)
copy their source pair's SQL (with Arabic names); they are re-linked to the source by that
SQL (`source_sql`), and dropped when the source no longer passes.

Run (laptop, records_db up, RECORDS_DB_URL in .env):
    uv run python -m sqlibrarian.training.validate_pairs
"""

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import psycopg

from sqlibrarian.evaluation.db import act_as_student, sample_students
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Lang
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.training.sql_utils import (
    arabic_output,
    course_names_used,
    filters_by_student,
    is_single_select,
    normalize_sql,
    picks_newest_term,
)

log = get_logger(__name__)


def run_readonly(conn: psycopg.Connection, sql: str, timeout: str) -> list[tuple] | None:
    """Run the query in a read-only transaction with a time limit.
    Returns the rows, or None if it failed. Nothing is ever saved."""
    with conn.cursor() as cur:
        try:
            cur.execute("BEGIN READ ONLY")
            cur.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
            cur.execute(sql)
            return cur.fetchall()
        except psycopg.Error:
            return None
        finally:
            cur.execute("ROLLBACK")


def check_students(cur: psycopg.Cursor, n: int) -> list[int]:
    """The scorer's sample students, then others spread evenly across all students."""
    students = sample_students(cur)
    cur.execute("SELECT student_id FROM public.students ORDER BY student_id")
    everyone = [r[0] for r in cur.fetchall()]
    step = max(1, len(everyone) // max(1, n - len(students)))
    for sid in everyone[::step]:
        if len(students) >= n:
            break
        if sid not in students:
            students.append(sid)
    return students


def has_value(rows: list[tuple]) -> bool:
    return any(v is not None for row in rows for v in row)


def tells_someone(cur: psycopg.Cursor, sql: str, students: list[int], needed: int) -> bool:
    """True if, for at least `needed` students alone, the answer has a non-NULL value.
    Only asked when the whole-database answer has none (a rare elective has values there
    even if no checked student took it). More than one: "last term" written as
    MAX(term_id) FROM enrollments is this term (no grades) for everyone except a
    dismissed student whose record stopped earlier."""
    found = 0
    for sid in students:
        act_as_student(cur, sid)
        cur.execute("SAVEPOINT q")
        try:
            cur.execute(sql)
            rows = cur.fetchall()
        except psycopg.Error:
            rows = []
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT q")
        found += has_value(rows)
        if found >= needed:
            return True
    return False


def localize(conn: psycopg.Connection, sql: str, lang: str, n_rows: int, timeout: str) -> str:
    """Arabic names for a non-English question, if the query still gives as many rows."""
    if lang == Lang.EN:
        return sql
    new = arabic_output(sql)
    rows = run_readonly(conn, new, timeout) if new != sql else None
    if rows is not None and len(rows) == n_rows:
        return new
    return sql


def pair_id(sql_key: str) -> str:
    return "own-" + hashlib.sha1(sql_key.encode("utf-8")).hexdigest()[:10]


def sync_rewrites(
    path: Path, kept: dict[str, dict], to_lang: Callable[[str, str], str] = lambda sql, _: sql
) -> None:
    """Keep rewrites whose source still passes, re-linked to the source's id and SQL.
    `kept` maps the normalized SQL of a kept pair (before and after its Arabic names) to
    the pair; `to_lang(sql, lang)` gives the SQL in the rewrite's language."""
    if not path.exists():
        return
    rows, dropped = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        source = kept.get(normalize_sql(r.get("source_sql") or r["reference_sql"]))
        if source is None:
            dropped += 1
            continue
        sql = source["reference_sql"]
        rows.append({**r, "id": f"{source['id']}-{r['lang']}", "source_id": source["id"],
                     "source_sql": sql, "reference_sql": to_lang(sql, r["lang"])})  # fmt: skip
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    log.info("validate.rewrites", kept=len(rows), dropped_source_gone=dropped, out=str(path))


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
    p.add_argument("--rewrites", type=Path, default=s.paths.own_pairs_arabic)
    args = p.parse_args()

    kept, seen, reasons, by_key, localized = [], set(), Counter(), {}, 0
    phrasings: Counter = Counter()  # (sql, lang) -> questions kept
    per_sql: Counter = Counter()  # sql -> questions kept, for the id suffix
    with (
        psycopg.connect(s.records_dsn, autocommit=True) as conn,
        psycopg.connect(s.records_dsn) as per_student,
        per_student.cursor() as cur,
    ):
        cur.execute("SELECT set_config('statement_timeout', %s, true)", (s.statement_timeout,))
        students = check_students(cur, s.validate_check_students)
        titles = conn.execute("SELECT title_en, title_ar FROM courses").fetchall()
        known_courses = {t for row in titles for t in row}
        for line in args.raw.read_text(encoding="utf-8").splitlines():
            batch = json.loads(line)
            for pair in batch["pairs"]:
                if not isinstance(pair, dict):
                    reasons["malformed"] += 1  # the model sometimes returns "" for a pair
                    continue
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
                if picks_newest_term(sql):
                    reasons["counts from the newest term"] += 1
                    continue
                rows = run_readonly(conn, sql, s.statement_timeout)
                if rows is None:
                    reasons["failed to run"] += 1
                    continue
                n_rows = len(rows)
                not_recorded = "not recorded" in batch["qtype"]
                if n_rows == 0 and not not_recorded:
                    reasons["no rows"] += 1
                    continue
                silent = not has_value(rows) and not tells_someone(
                    cur, sql, students, s.validate_min_students
                )
                if silent and not not_recorded:
                    reasons["empty for every student"] += 1
                    continue
                raw_key, lang = normalize_sql(sql), batch_lang(batch)
                new = localize(conn, sql, lang, n_rows, s.statement_timeout)
                localized += new != sql
                sql = new
                key = normalize_sql(sql)
                asked = " ".join(question.lower().split())
                if (key, asked) in seen:
                    reasons["duplicate"] += 1
                    continue
                if phrasings[key, lang] >= s.max_phrasings_per_sql:
                    reasons["enough phrasings of this SQL"] += 1
                    continue
                seen.add((key, asked))
                phrasings[key, lang] += 1
                per_sql[key] += 1
                first = by_key.get(key)
                row = {
                    "id": pair_id(key) + (f"-{per_sql[key]}" if per_sql[key] > 1 else ""),
                    "question": question,
                    "reference_sql": sql,
                    "qtype": batch["qtype"],
                    "difficulty": batch["difficulty"],
                    "lang": lang,
                    "rows": n_rows,
                }
                if first is not None:
                    row["source_id"] = first["id"]  # same query: same group as the first
                kept.append(row)
                by_key.setdefault(key, row)  # rewrites follow the first phrasing
                by_key.setdefault(raw_key, row)  # old rewrites hold the SQL before

        def to_lang(sql: str, lang: str) -> str:
            rows = run_readonly(conn, sql, s.statement_timeout) or []
            return localize(conn, sql, lang, len(rows), s.statement_timeout)

        sync_rewrites(args.rewrites, by_key, to_lang)
        per_student.rollback()

    with args.out.open("w", encoding="utf-8") as f:
        for row in kept:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info(
        "validate.done", kept=len(kept), total=len(kept) + sum(reasons.values()),
        out=str(args.out), dropped=dict(reasons.most_common()), arabic_names=localized,
    )  # fmt: skip
    log.info(
        "validate.by_type", **{q[:40]: n for q, n in Counter(r["qtype"] for r in kept).items()}
    )
    log.info("validate.by_lang", **Counter(r["lang"] for r in kept))


if __name__ == "__main__":
    main()
