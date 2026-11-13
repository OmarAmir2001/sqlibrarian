"""Step 2.2 (1-3): generate question + SQL pairs for YOUR schema with Groq's free tier.

Free-tier limits for the generator model (settings.pair_generator_model): 8,000
tokens a minute and 200,000 a day. One call is about 6,000 tokens, so the script
waits between calls and stops after --max-calls. Run it once a day; it appends to
the same file, so nothing is lost between days.

Run (laptop, with GROQ_API_KEY and RECORDS_DB_URL in .env):
    uv run python -m sqlibrarian.training.generate_pairs
    uv run python -m sqlibrarian.training.generate_pairs --dry-run   # show the prompt only
"""

import argparse
import itertools
import json
import random
import time
from pathlib import Path

import psycopg

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Difficulty, Lang
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.training.groq_client import CALL_ERRORS, make_client

log = get_logger(__name__)

QUESTION_TYPES = [
    "the grade, total mark or one mark (midterm, coursework, final) in one course",
    "the overall (cumulative) GPA: what it is, whether it is above or below a number, "
    "the standing it gives (SELECT cumulative_gpa FROM students, never computed)",
    "GPA in one named term or the last term (computed from that term's grades)",
    "courses failed, courses repeated, or courses failed and not passed yet",
    "credit hours passed so far, this term, or electives passed",
    "required or elective courses still left in the student's study plan",
    "courses the student can take next term (prerequisites passed or in progress)",
    "prerequisites the student is still missing for a named course",
    "the student's plan for next term: its status, its courses, its credit hours",
    "this term: registered courses, hours, and marks that are not recorded yet",
    "comparing terms (best term, worst term, improvement between two terms)",
    "counting things (courses passed in level 2, electives passed, attempts at one course)",
    "the student's advisor, program, level, or most credit hours allowed next term",
    "academic standing and warnings: the student's standing, warnings in a row or in total, "
    "how many more warnings before dismissal (registration_policy limits)",
    "excuses and improvement plans: excuses taken (which year, which type), the improvement "
    "plan's terms and outcome",
    "registration: is registration open now, its dates, courses open this term "
    "(course_offerings), graduates-only courses, the minimum hours per term",
    "graduation: hours left to graduate, graduation candidate, max electives, "
    "study-plan courses of a level or semester (courses.level, courses.semester)",
]

# The language each call asks for, as written in the prompt. English twice: Egyptian
# and mixed Arabic are added later by rewrite_arabic.py.
LANGUAGES = [Lang.EN, Lang.AR, Lang.EN]
LANGUAGE_IN_PROMPT = {Lang.EN: "English", Lang.AR: "Modern Standard Arabic"}

PROMPT = """You write training data for a text-to-SQL model used by university students.

Database (PostgreSQL):
{schema}

Rules:
- Every question is asked BY the current student about THEIR OWN record ("my GPA", "did I pass...").
- Never filter by student at all: no student_id = ..., no :student_id or ? placeholders, no
  current_user. The database already limits every table to the current student's rows.
- One PostgreSQL SELECT per question. No INSERT, UPDATE, DELETE.
- Use only the tables and columns above, each on its own table (credit_hours is in courses).
- Find a course by its name (courses.title_en or title_ar), never by guessing its code.
  Use only courses from the list, with their names written exactly as in the list.
- "My GPA" or "cumulative GPA" is ALWAYS just: SELECT cumulative_gpa FROM students.
  Never compute it. Only a GPA for one term (a named term, the last term) is computed:
  SUM(grade_points * credit_hours) / SUM(credit_hours) over that term's passed and failed courses.
- Enrollment status: 'passed' and 'failed' are finished courses; this term's courses are
  'in_progress' (no grade yet). "This term" means terms.is_current is true AND
  status = 'in_progress'; "next term" is the one after it. Only filter
  status IN ('passed', 'failed') when the question is about finished courses.
- Select every column the question needs: "my grade" means letter_grade and total.
  A student may take a course more than once, so use DISTINCT when listing courses.
- Arabic questions select the Arabic names (title_ar); English questions select title_en.
- Advisor: students.advisor_ta_id joined to tas. Credit limit: credit_rules by
  students.cumulative_gpa (21 hours when it is NULL). Plans: plans joined to plan_courses.
- Standing, warnings, hours passed and graduation candidate are columns of students
  (academic_standing, consecutive_warnings, cumulative_warnings, completed_credit_hours,
  graduation_candidate): select them, never compute them.
- Rule numbers (warning GPA, dismissal warnings, minimum hours, graduation hours, electives)
  are rows: SELECT value FROM registration_policy WHERE rule_key = '...'.
- Courses open this term: course_offerings joined to the current term. Registration is open
  when CURRENT_DATE is between the term's registration_opens and registration_closes.
- Study-plan placement of a course: courses.level (year 1-4) and courses.semester.
- Write questions the way real students write: short, informal, sometimes with typos.

Examples of good pairs:
{examples}

Write {n} NEW pairs, all different from the examples and from each other.
Question type for this batch: {qtype}
Difficulty: {difficulty}
Language: {language}

Return JSON only: {{"pairs": [{{"question": "...", "sql": "..."}}]}}"""


def load_examples(path: Path, k: int = 10) -> str:
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    picked = random.sample(rows, min(k, len(rows)))
    return "\n".join(f"Q: {r['question']}\nSQL: {r['reference_sql']}" for r in picked)


def with_course_list(schema: str) -> str:
    """Add every course's code and names to the schema, for Groq only. Without it, the
    generator invents course names ("Database Systems") that the institute doesn't have.
    The model being trained never sees this list; it reads the plain schema."""
    with psycopg.connect(get_settings().records_dsn) as conn:
        rows = conn.execute(
            "SELECT course_code, title_en, title_ar FROM courses ORDER BY course_code"
        ).fetchall()
    lines = "\n".join(f"{code} | {en} | {ar}" for code, en, ar in rows)
    return f"{schema}\nCourses (code | title_en | title_ar), use exactly these names:\n{lines}\n"


def rotation() -> list[tuple[str, Difficulty, Lang]]:
    """Every (question type, difficulty, language) once, with the question type changing
    fastest: any 13 calls in a row cover all 13 types, so each day's batch is balanced."""
    return [
        (qtype, difficulty, lang)
        for difficulty, lang in itertools.product(Difficulty, LANGUAGES)
        for qtype in QUESTION_TYPES
    ]


def build_prompt(schema: str, examples: Path, n: int, qtype: str, difficulty, lang) -> str:
    return PROMPT.format(
        schema=schema, examples=load_examples(examples), n=n, qtype=qtype,
        difficulty=difficulty, language=LANGUAGE_IN_PROMPT[lang],
    )  # fmt: skip


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--schema", type=Path, default=s.paths.schema)
    p.add_argument("--examples", type=Path, default=s.paths.dev_questions)
    p.add_argument("--out", type=Path, default=s.paths.raw_generated)
    p.add_argument("--max-calls", type=int, default=s.groq_max_calls_per_day)
    p.add_argument("--pairs-per-call", type=int, default=20)
    p.add_argument("--wait", type=float, default=s.groq_wait_seconds)
    p.add_argument("--model", default=s.pair_generator_model)
    p.add_argument("--dry-run", action="store_true", help="print the first prompt, call nothing")
    args = p.parse_args()

    schema = with_course_list(args.schema.read_text(encoding="utf-8"))
    if args.dry_run:
        prompt = build_prompt(
            schema, args.examples, args.pairs_per_call,
            QUESTION_TYPES[0], Difficulty.EASY, LANGUAGES[0],
        )  # fmt: skip
        print(prompt)  # the command's output, not a log line
        log.info("dry_run", characters=len(prompt), approx_tokens=len(prompt) // 4)
        return

    client = make_client()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Continue the rotation from where yesterday's run stopped (one line per call).
    done = sum(1 for _ in args.out.open(encoding="utf-8")) if args.out.exists() else 0
    plan = itertools.islice(itertools.cycle(rotation()), done, None)
    log.info("groq.start", model=args.model, max_calls=args.max_calls, calls_done_before=done)

    used_tokens = 0
    for call, (qtype, difficulty, lang) in enumerate(plan, 1):
        if call > args.max_calls:
            break
        prompt = build_prompt(schema, args.examples, args.pairs_per_call, qtype, difficulty, lang)
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"},  # fewer hidden reasoning tokens
            )
            pairs = json.loads(resp.choices[0].message.content)["pairs"]
        except CALL_ERRORS as e:
            # rate limit or a reply that isn't the JSON we asked for: wait, move on
            log.warning("groq.call_failed", call=call, error=type(e).__name__, detail=str(e)[:200])
            time.sleep(args.wait * 2)
            continue

        used_tokens += resp.usage.total_tokens
        with args.out.open("a", encoding="utf-8") as f:
            row = {
                "qtype": qtype,
                "difficulty": difficulty,
                "lang": lang,
                "language": LANGUAGE_IN_PROMPT[lang],  # kept for older readers
                "pairs": pairs,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        log.info(
            "groq.call_done", call=call, of=args.max_calls, pairs=len(pairs),
            tokens=resp.usage.total_tokens, tokens_total=used_tokens,
        )  # fmt: skip
        time.sleep(args.wait)
    log.info("groq.finished", tokens_total=used_tokens, out=str(args.out))


if __name__ == "__main__":
    main()
