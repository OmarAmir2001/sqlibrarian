"""Write the test set in a spreadsheet, check it as you go, then build the frozen file.

    uv run python -m sqlibrarian.evaluation.test_set template   # creates the empty CSV
    uv run python -m sqlibrarian.evaluation.test_set check      # run as often as you like
    uv run python -m sqlibrarian.evaluation.test_set build      # writes test_questions.jsonl

You edit data/eval/test_questions.csv (Excel: save as "CSV UTF-8"; Google Sheets:
File > Download > CSV). Columns: lang, difficulty, kind, question, reference_sql,
notes. See docs/test_set_guide.md for the rules and examples.

`check` reports every problem with its spreadsheet row number:
  ERROR (blocks build)  missing or invalid field, not exactly one SELECT, filters by
                        student, unknown course name, the query fails, no student has
                        any answer, the same question twice
  warning               almost the same as a dev question (would leak, since dev
                        questions guide the training data); answer is only empty/zero
and shows progress toward the target per language. Queries run read-only, as the
sample students, and are rolled back.

`build` refuses while there are errors, then writes ids test-001, test-002, ... in row
order. Don't reorder or delete rows after scoring starts, or ids shift.
"""

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path

import psycopg

from sqlibrarian.evaluation.db import act_as_student, sample_students
from sqlibrarian.evaluation.score import run, says_something
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Difficulty, Lang, QuestionKind
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.training.sql_utils import course_names_used, filters_by_student, is_single_select

log = get_logger(__name__)

COLUMNS = ["lang", "difficulty", "kind", "question", "reference_sql", "notes"]
REQUIRED = ["lang", "difficulty", "kind", "question", "reference_sql"]
TEST_LANGS = (Lang.EN, Lang.AR, Lang.MIXED)  # Egyptian Arabic counts as mixed
DEV_SIMILARITY_WARNING = 0.9  # question text this close to a dev question is a leak


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "warning"


@dataclass
class Problem:
    row: int  # spreadsheet row number (the header is row 1)
    severity: Severity
    message: str


# --- reading ---------------------------------------------------------------------------


def read_rows(path: Path) -> list[tuple[int, dict]]:
    """(spreadsheet row number, row) for every non-empty row. utf-8-sig accepts the
    byte-order mark Excel adds."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"{path.name} is missing columns {missing}; expected {COLUMNS}")
        rows = []
        for n, row in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in row.items() if k}
            if any(row.get(c) for c in REQUIRED):
                rows.append((n, row))
    return rows


_TASHKEEL = re.compile(r"[ً-ْـ]")  # Arabic diacritics and tatweel


def normalize_question(text: str) -> str:
    """Lower-case, unify common Arabic letter variants, drop punctuation and spaces,
    so trivially different spellings of one question compare equal."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _TASHKEEL.sub("", text)
    text = text.translate(str.maketrans("أإآىة", "اااي" + "ه"))
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


# --- checks ----------------------------------------------------------------------------


def check_fields(n: int, row: dict, known_courses: set[str]) -> list[Problem]:
    """Checks that need no database."""
    problems = []

    def error(msg: str) -> None:
        problems.append(Problem(n, Severity.ERROR, msg))

    for col in REQUIRED:
        if not row.get(col):
            error(f"{col} is empty")
    if row.get("lang") and row["lang"] not in TEST_LANGS:
        error(f"lang must be one of {[str(x) for x in TEST_LANGS]}, not {row['lang']!r}")
    if row.get("difficulty") and row["difficulty"] not in iter(Difficulty):
        error(f"difficulty must be one of {[str(x) for x in Difficulty]}")
    if row.get("kind") and row["kind"] not in iter(QuestionKind):
        error(f"kind must be one of {[str(x) for x in QuestionKind]}")

    sql = row.get("reference_sql", "").rstrip(";").strip()
    if sql:
        if not is_single_select(sql):
            error("reference_sql is not exactly one SELECT (or it doesn't parse)")
        else:
            if filters_by_student(sql):
                error("reference_sql filters by student; write it for 'the current student'")
            unknown = course_names_used(sql) - known_courses
            if unknown:
                error(f"unknown course name(s) {sorted(unknown)}: use the exact catalog titles")
    return problems


def check_duplicates(rows: list[tuple[int, dict]], dev_questions: list[str]) -> list[Problem]:
    problems, seen = [], {}
    dev = [(q, normalize_question(q)) for q in dev_questions]
    for n, row in rows:
        key = normalize_question(row.get("question", ""))
        if not key:
            continue
        if key in seen:
            problems.append(Problem(n, Severity.ERROR, f"same question as row {seen[key]}"))
        seen.setdefault(key, n)
        for original, dev_key in dev:
            if SequenceMatcher(None, key, dev_key).ratio() >= DEV_SIMILARITY_WARNING:
                problems.append(
                    Problem(n, Severity.WARNING, f"almost the same as dev question {original!r}")
                )
                break
    return problems


def check_answers(
    cur: psycopg.Cursor, n: int, sql: str, sample: list[int], everyone: list[int]
) -> list[Problem]:
    """Run the reference as the sample students; if none has a meaningful answer,
    look for any student who does (the scorer does the same)."""
    answers = []
    for sid in sample:
        act_as_student(cur, sid)
        rows, error = run(cur, sql)
        if error:
            return [Problem(n, Severity.ERROR, f"query fails: {error}")]
        answers.append(rows)
    if any(says_something(a) for a in answers):
        return []
    for sid in everyone:
        act_as_student(cur, sid)
        rows, _ = run(cur, sql)
        if says_something(rows):
            return []
    if any(answers):
        return [Problem(n, Severity.WARNING, "answer is only empty/NULL/0 for every student")]
    return [Problem(n, Severity.ERROR, "no student has any answer: the question tests nothing")]


def check_all(path: Path) -> tuple[list[tuple[int, dict]], list[Problem]]:
    s = get_settings()
    rows = read_rows(path)
    lines = s.paths.dev_questions.read_text("utf-8").splitlines()
    dev_questions = [json.loads(x)["question"] for x in lines if x.strip()]
    with psycopg.connect(s.records_dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('statement_timeout', %s, true)", (s.statement_timeout,))
        titles = cur.execute("SELECT title_en, title_ar FROM public.courses").fetchall()
        known_courses = {t for pair in titles for t in pair}
        sample = sample_students(cur)
        cur.execute("SELECT student_id FROM public.students ORDER BY student_id")
        everyone = [r[0] for r in cur.fetchall()]

        problems = []
        for n, row in rows:
            static = check_fields(n, row, known_courses)
            problems += static
            if not any(p.severity is Severity.ERROR for p in static):
                sql = row["reference_sql"].rstrip(";").strip()
                problems += check_answers(cur, n, sql, sample, everyone)
        conn.rollback()
    problems += check_duplicates(rows, dev_questions)
    return rows, sorted(problems, key=lambda p: (p.row, p.severity))


def report(rows: list[tuple[int, dict]], problems: list[Problem]) -> None:
    """Printed, not logged: this is what the command is for."""
    target = get_settings().test_set_per_lang
    for p in problems:
        print(f"row {p.row:4d}  {p.severity:7s}  {p.message}")
    by_lang = Counter(r.get("lang") for _, r in rows)
    print(f"\n{len(rows)} questions; target {target} per language:")
    for lang in TEST_LANGS:
        print(f"  {lang:6s} {by_lang.get(lang, 0):4d} / {target}")
    print("difficulty:", dict(Counter(r.get("difficulty") for _, r in rows)))
    print("kind:      ", dict(Counter(r.get("kind") for _, r in rows).most_common()))
    errors = sum(p.severity is Severity.ERROR for p in problems)
    warnings = len(problems) - errors
    print(f"{errors} errors, {warnings} warnings")


# --- commands --------------------------------------------------------------------------


def template() -> None:
    path = get_settings().paths.test_questions_csv
    if path.exists():
        raise SystemExit(f"{path} already exists; not overwriting your questions")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerow(COLUMNS)
    log.info("test_set.template_written", path=str(path))


def check(path: Path) -> int:
    rows, problems = check_all(path)
    report(rows, problems)
    errors = sum(p.severity is Severity.ERROR for p in problems)
    log.info("test_set.checked", questions=len(rows), errors=errors, file=str(path))
    return 1 if errors else 0


def build(path: Path, out: Path) -> int:
    rows, problems = check_all(path)
    if any(p.severity is Severity.ERROR for p in problems):
        report(rows, problems)
        log.error("test_set.build_refused", reason="fix the errors above first")
        return 1
    with out.open("w", encoding="utf-8") as f:
        for i, (_, row) in enumerate(rows, start=1):
            item = {
                "id": f"test-{i:03d}",
                "question": row["question"],
                "lang": row["lang"],
                "difficulty": row["difficulty"],
                "kind": row["kind"],
                "reference_sql": row["reference_sql"].rstrip(";").strip(),
            }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    log.info("test_set.built", questions=len(rows), path=str(out))
    return 0


def main() -> None:
    configure_logging()
    paths = get_settings().paths
    p = argparse.ArgumentParser(description="Write, check and build the test set.")
    p.add_argument("command", choices=["template", "check", "build"])
    p.add_argument("--file", type=Path, default=paths.test_questions_csv, help="the CSV")
    p.add_argument("--out", type=Path, default=paths.test_questions, help="build output")
    args = p.parse_args()
    if args.command == "template":
        template()
        raise SystemExit(0)
    raise SystemExit(check(args.file) if args.command == "check" else build(args.file, args.out))


if __name__ == "__main__":
    main()
