"""The ONE place that builds what the model sees, and reads back what it wrote.

Training, evaluation and the API all import build_messages() and extract_sql(),
so the model always gets exactly the same layout.
"""

import re

PROMPT_VERSION = "v1"

SYSTEM = "Write one PostgreSQL SELECT query that answers the question. Return only the SQL."

# Used only when examples are asked for (the few-shot baseline). Written for this
# schema, and different from every dev and test question.
FEW_SHOT = [
    (
        "How many courses did I pass in spring 2025?",
        "SELECT COUNT(*) FROM enrollments e JOIN terms t ON t.term_id = e.term_id "
        "WHERE t.year = 2025 AND t.season = 'spring' AND e.status = 'passed'",
    ),
    (
        "ما المقررات التي حصلت فيها على تقدير A+؟",
        "SELECT c.course_code, c.title_ar FROM enrollments e "
        "JOIN courses c ON c.course_code = e.course_code WHERE e.letter_grade = 'A+'",
    ),
    (
        "عندي كام مادة elective لحد دلوقتي؟",
        "SELECT COUNT(DISTINCT e.course_code) FROM enrollments e "
        "JOIN courses c ON c.course_code = e.course_code "
        "WHERE e.status = 'passed' AND NOT c.is_required",
    ),
]


def build_messages(
    schema_text: str,
    question: str,
    sql: str | None = None,
    examples: list[tuple[str, str]] | None = None,
) -> list[dict]:
    """Return chat messages. Leave sql=None for evaluation and the API.
    `examples` = (question, sql) pairs shown after the schema, e.g. FEW_SHOT."""
    user = f"Database:\n{schema_text}\n\n"
    if examples:
        shown = "\n\n".join(f"Question: {q}\nSQL: {s}" for q, s in examples)
        user += f"Examples:\n{shown}\n\n"
    user += f"Question: {question}"
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
    if sql is not None:
        messages.append({"role": "assistant", "content": sql.strip()})
    return messages


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:sql|postgresql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_START = re.compile(r"\b(SELECT|WITH)\b", re.IGNORECASE)


def extract_sql(text: str) -> str:
    """Pull the query out of a model reply: drop thinking blocks and code fences,
    start at the first SELECT or WITH, stop at the first semicolon.
    Returns "" when there is no query."""
    text = _THINK.sub("", text)
    if fence := _FENCE.search(text):
        text = fence.group(1)
    start = _START.search(text)
    if not start:
        return ""
    return text[start.start() :].split(";", 1)[0].strip()
