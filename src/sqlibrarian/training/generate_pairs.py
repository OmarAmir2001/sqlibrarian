"""Step 2.2 (1-3): generate question + SQL pairs for YOUR schema with Groq's free tier.

Free-tier limits for openai/gpt-oss-120b: 8,000 tokens a minute and 200,000 a
day. One call is about 5,500 tokens, so the script waits between calls and
stops after --max-calls. Run it once a day; it appends to the same file, so
nothing is lost between days.

Run (laptop, with GROQ_API_KEY in .env):
    uv run python -m sqlibrarian.training.generate_pairs --max-calls 34
"""

import argparse
import itertools
import json
import os
import random
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

QUESTION_TYPES = [
    "the grade, total mark or one mark (midterm, coursework, final) in one course",
    "cumulative GPA, or GPA in one named term or the last term",
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
]
DIFFICULTIES = ["easy", "medium", "hard"]

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
- Cumulative GPA is students.cumulative_gpa. A term GPA is
  SUM(grade_points * credit_hours) / SUM(credit_hours) over passed and failed courses.
- "This term" is the term where terms.is_current is true; "next term" is the one after it.
- Write questions the way real students write: short, informal, sometimes with typos.

Examples of good pairs:
{examples}

Write {n} NEW pairs, all different from the examples and from each other.
Question type for this batch: {qtype}
Difficulty: {difficulty}
Language: {language}

Return JSON only: {{"pairs": [{{"question": "...", "sql": "..."}}]}}"""

LANGUAGES = [
    "English",
    "Modern Standard Arabic",
    "English",
]  # Egyptian/mixed comes later, in rewrite_arabic.py


def load_examples(path: Path, k: int = 10) -> str:
    rows = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    picked = random.sample(rows, min(k, len(rows)))
    return "\n".join(f"Q: {r['question']}\nSQL: {r['reference_sql']}" for r in picked)


def with_course_list(schema: str) -> str:
    """Add every course's code and names to the schema, for Groq only. Without it, the
    generator invents course names ("Database Systems") that the institute doesn't have.
    The model being trained never sees this list; it reads the plain schema."""
    import psycopg

    from sqlibrarian.config import get_settings

    with psycopg.connect(get_settings().records_db_url) as conn:
        rows = conn.execute(
            "SELECT course_code, title_en, title_ar FROM courses ORDER BY course_code"
        ).fetchall()
    lines = "\n".join(f"{code} | {en} | {ar}" for code, en, ar in rows)
    return f"{schema}\nCourses (code | title_en | title_ar), use exactly these names:\n{lines}\n"


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("--schema", type=Path, default=Path("data/eval/schema.txt"))
    p.add_argument("--examples", type=Path, default=Path("data/eval/dev_questions.jsonl"))
    p.add_argument("--out", type=Path, default=Path("data/training/raw_generated.jsonl"))
    p.add_argument("--max-calls", type=int, default=34)  # ~5,500 tokens each: under 200K/day
    p.add_argument("--pairs-per-call", type=int, default=20)
    p.add_argument("--wait", type=float, default=45.0)  # seconds between calls (8K tokens/min)
    p.add_argument("--model", default="openai/gpt-oss-120b")
    p.add_argument("--dry-run", action="store_true", help="print the first prompt, call nothing")
    args = p.parse_args()

    if args.dry_run:
        schema = with_course_list(args.schema.read_text(encoding="utf-8"))
        qtype, difficulty, language = QUESTION_TYPES[0], DIFFICULTIES[0], LANGUAGES[0]
        prompt = PROMPT.format(
            schema=schema, examples=load_examples(args.examples), n=args.pairs_per_call,
            qtype=qtype, difficulty=difficulty, language=language,
        )  # fmt: skip
        print(prompt)
        print(f"\n--- {len(prompt)} characters, about {len(prompt) // 4} tokens in (rough)")
        return

    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"])
    schema = with_course_list(args.schema.read_text(encoding="utf-8"))
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Continue the rotation from where yesterday's run stopped.
    done = sum(1 for _ in args.out.open(encoding="utf-8")) if args.out.exists() else 0
    plan = itertools.islice(
        itertools.cycle(itertools.product(QUESTION_TYPES, DIFFICULTIES, LANGUAGES)),
        done,
        None,
    )

    used_tokens = 0
    for call, (qtype, difficulty, language) in enumerate(plan):
        if call >= args.max_calls:
            break
        prompt = PROMPT.format(
            schema=schema,
            examples=load_examples(args.examples),
            n=args.pairs_per_call,
            qtype=qtype,
            difficulty=difficulty,
            language=language,
        )
        try:
            resp = client.chat.completions.create(
                model=args.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.9,
                response_format={"type": "json_object"},
                extra_body={"reasoning_effort": "low"},  # fewer hidden reasoning tokens
            )
            pairs = json.loads(resp.choices[0].message.content)["pairs"]
        except Exception as e:  # rate limit, bad JSON: log it, wait, move on
            print(f"call {call}: failed ({type(e).__name__}: {e}); waiting and continuing")
            time.sleep(args.wait * 2)
            continue

        used_tokens += resp.usage.total_tokens
        with args.out.open("a", encoding="utf-8") as f:
            # one line per CALL, so the rotation above can count calls
            f.write(
                json.dumps(
                    {
                        "qtype": qtype,
                        "difficulty": difficulty,
                        "language": language,
                        "pairs": pairs,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        print(
            f"call {call + 1}/{args.max_calls}: {len(pairs)} pairs, {used_tokens:,} tokens so far"
        )
        time.sleep(args.wait)


if __name__ == "__main__":
    main()
