"""Renamed copies of the database, so the model learns to READ the schema, not remember it.

A model fine-tuned on one schema learns its names as habits (`cumulative_gpa`,
`enrollments`) and writes them even when the prompt shows a different database. To stop
that, each own training pair is also shown with a *variant*: the same data under other
table and column names (`students` -> `learners`, `cumulative_gpa` -> `cgpa`), with the
schema text and the SQL renamed together. The question and the answer stay the same, so
the only way to get the SQL right is to read the names from the prompt.

- Variants come from fixed synonym lists, drawn with a seed (same variants every run).
- One variant, HELDOUT, is never used in training: its names (styled after the
  institute's own system) are a test of a schema the model has never seen.
- The SQL is renamed token by token, so its writing style stays as it was. Every
  renamed query is then checked against PostgreSQL views with the new names
  (`view_ddl`): it is kept only if it returns exactly the rows of the original.

Run (records_db up):
    uv run python -m sqlibrarian.training.schema_variants export-heldout
writes data/eval/schema_heldout.txt and data/eval/dev_questions_heldout.jsonl (checked).
"""

import argparse
import json
import random
import re
from dataclasses import dataclass, field

import psycopg
import sqlglot
from sqlglot.tokens import TokenType

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

# Other names real student systems use. HELDOUT's names are kept out of these lists.
TABLE_SYNONYMS = {
    "terms": ["semesters", "academic_sessions", "term_list", "sessions"],
    "courses": ["subjects", "course_master", "course_list", "modules"],
    "prerequisites": ["prereqs", "course_requirements", "prerequisite_links"],
    "course_offerings": ["offered_courses", "term_courses", "course_schedule"],
    "registration_policy": ["policy_rules", "registration_rules", "rule_settings"],
    "credit_rules": ["load_limits", "max_load_rules", "hour_limits"],
    "grading_scale": ["grade_bands", "letter_grades", "grade_table"],
    "tas": ["teaching_assistants", "staff_advisors", "tutors"],
    "students": ["learners", "student_master", "pupils", "student_info"],
    "enrollments": ["registrations", "student_courses", "transcript", "course_registrations"],
    "excuses": ["leave_requests", "student_leaves", "absence_excuses"],
    "improvement_plans": ["probation_plans", "recovery_plans", "academic_plans"],
    "plans": ["study_plans", "course_requests", "registration_requests"],
    "plan_courses": ["study_plan_items", "request_courses", "plan_items"],
}
COLUMN_SYNONYMS = {
    "term_id": ["semester_id", "session_id", "term_no"],
    "year": ["start_year", "yr"],
    "season": ["term_season", "part_of_year"],
    "is_current": ["current", "is_active", "active_now"],
    "registration_opens": ["reg_start", "registration_start"],
    "registration_closes": ["reg_end", "registration_end"],
    "course_code": ["subject_code", "crs_code", "course_no"],
    "title_en": ["name_english", "english_title", "title"],
    "title_ar": ["name_arabic", "arabic_title", "title_arabic"],
    "credit_hours": ["credits", "hours", "credit_units"],
    "level": ["year_level", "stage", "lvl"],
    "semester": ["plan_term", "term_in_plan"],
    "program": ["major", "track", "specialization"],
    "is_required": ["compulsory", "is_core", "required"],
    "min_passed_hours": ["min_hours_passed", "hours_needed"],
    "required_course_code": ["prereq_code", "needs_course", "required_subject"],
    "graduates_only": ["final_year_only", "seniors_only"],
    "rule_key": ["rule", "rule_code", "setting"],
    "value": ["amount", "number_value", "setting_value"],
    "description": ["meaning", "details", "notes"],
    "term_type": ["session_type", "kind_of_term"],
    "gpa_from": ["min_gpa", "lower_gpa"],
    "gpa_to": ["max_gpa", "upper_gpa"],
    "max_credit_hours": ["max_hours", "hour_limit", "max_load"],
    "letter_grade": ["grade", "letter", "grade_letter_code"],
    "min_total": ["min_mark", "lowest_mark", "from_mark"],
    "points": ["gpa_points", "grade_value", "point_value"],
    "ta_id": ["staff_id", "tutor_id", "assistant_id"],
    "name": ["full_name", "staff_name", "display_name"],
    "student_id": ["learner_id", "sid", "student_no"],
    "name_ar": ["arabic_name", "full_name_ar"],
    "name_en": ["english_name", "full_name_en"],
    "cumulative_gpa": ["gpa", "overall_gpa", "cum_gpa"],
    "completed_credit_hours": ["hours_completed", "earned_hours", "passed_hours"],
    "academic_standing": ["standing", "academic_status", "status_code"],
    "consecutive_warnings": ["warnings_in_a_row", "consec_warnings"],
    "cumulative_warnings": ["total_warnings", "all_warnings"],
    "graduation_candidate": ["near_graduation", "can_graduate", "is_graduating"],
    "advisor_ta_id": ["advisor_id", "tutor", "advisor_staff_id"],
    "enrollment_id": ["registration_id", "row_id", "attempt_id"],
    "midterm": ["mid_mark", "midterm_mark", "mid_exam"],
    "coursework": ["work_mark", "assignments_mark", "cw"],
    "final": ["final_mark", "final_exam", "exam_mark"],
    "total": ["total_score", "overall_mark", "mark"],
    "grade_points": ["points_earned", "gp", "quality_point"],
    "status": ["state", "result", "outcome_code"],
    "excuse_id": ["leave_id", "request_id"],
    "academic_year": ["school_year", "year_label"],
    "excuse_type": ["leave_type", "kind"],
    "improvement_plan_id": ["probation_id", "recovery_id"],
    "start_term_id": ["from_term", "begin_term_id"],
    "evaluation_term_id": ["review_term", "check_term_id"],
    "outcome": ["result_code", "plan_result"],
    "plan_id": ["request_id", "study_plan_id"],
    "created_at": ["submitted_at", "sent_on", "created_on"],
    "reviewed_by": ["approved_by", "reviewer_id", "checked_by"],
    "reviewed_at": ["approved_at", "reviewed_on", "checked_at"],
}
TABLE_PREFIXES = ["tbl_", "sis_", "t_"]


@dataclass(frozen=True)
class Variant:
    name: str
    tables: dict[str, str] = field(default_factory=dict)  # old -> new (renamed only)
    columns: dict[str, str] = field(default_factory=dict)  # old -> new, same in every table


# Styled after the institute's own system; never used in training.
HELDOUT = Variant(
    name="heldout",
    tables={
        "terms": "academic_terms", "courses": "course_catalog",
        "prerequisites": "course_prerequisites", "course_offerings": "term_offerings",
        "registration_policy": "academic_rules", "credit_rules": "credit_hour_limits",
        "grading_scale": "grade_scale", "tas": "advisors", "students": "student_profiles",
        "enrollments": "course_attempts", "excuses": "student_excuses",
        "improvement_plans": "improvement_records", "plans": "advising_requests",
        "plan_courses": "advising_request_courses",
    },
    columns={
        "term_id": "term_ref", "is_current": "current_flag", "season": "semester_type",
        "course_code": "course_ref", "title_en": "course_name_en", "title_ar": "course_name_ar",
        "level": "academic_level", "semester": "plan_semester", "program": "department",
        "is_required": "mandatory", "min_passed_hours": "min_completed_hours",
        "required_course_code": "prereq_course_ref", "rule_key": "rule_name",
        "value": "rule_value", "max_credit_hours": "max_hours_allowed",
        "letter_grade": "grade_letter", "ta_id": "advisor_no", "name": "advisor_name",
        "student_id": "student_ref", "cumulative_gpa": "cgpa",
        "completed_credit_hours": "completed_hours", "academic_standing": "standing_code",
        "consecutive_warnings": "consecutive_warning_count",
        "cumulative_warnings": "cumulative_warning_count", "advisor_ta_id": "advisor_ref",
        "grade_points": "quality_points", "total": "total_mark", "status": "attempt_status",
        "excuse_type": "excuse_kind", "outcome": "plan_outcome", "plan_id": "request_ref",
        "reviewed_by": "reviewed_by_advisor",
    },
)  # fmt: skip


def parse_schema(text: str) -> dict[str, list[str]]:
    """Table -> its columns, in order, from the schema text."""
    tables: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if m := re.match(r"TABLE (\w+):", line):
            current = tables.setdefault(m.group(1), [])
        elif current is not None and (m := re.match(r"  (\w+) ", line)):
            current.append(m.group(1))
    return tables


def is_consistent(v: Variant, tables: dict[str, list[str]]) -> bool:
    """New names never collide: each new column name stands for one old name (so a query
    can't become ambiguous) and no table takes another's name."""
    final_tables = [v.tables.get(t, t) for t in tables]
    if len(set(final_tables)) != len(final_tables):
        return False
    all_columns = {c for cols in tables.values() for c in cols}
    meaning: dict[str, str] = {}
    for old in all_columns:
        new = v.columns.get(old, old)
        if meaning.setdefault(new, old) != old or new in final_tables:
            return False
    return True


def make_variants(
    n: int, seed: int, schema_text: str, table_share: float = 0.8, column_share: float = 0.6
) -> list[Variant]:
    """n different training variants (never HELDOUT's names)."""
    tables = parse_schema(schema_text)
    rng = random.Random(seed)
    out: list[Variant] = []
    while len(out) < n:
        prefix = rng.choice(TABLE_PREFIXES) if rng.random() < 0.25 else ""
        t_map = {
            t: prefix + (rng.choice(TABLE_SYNONYMS[t]) if rng.random() < table_share else t)
            for t in tables
        }
        t_map = {k: v for k, v in t_map.items() if v != k}
        c_map = {
            c: rng.choice(names)
            for c, names in COLUMN_SYNONYMS.items()
            if rng.random() < column_share
        }
        v = Variant(f"v{len(out) + 1:02d}", t_map, c_map)
        if is_consistent(v, tables) and v not in out:
            out.append(v)
    return out


def _rename_words(text: str, v: Variant) -> str:
    """Identifiers mentioned inside comments: only snake_case names (credit_hours,
    is_current), so prose words like "level" or "status" stay untouched."""
    names = {**{k: x for k, x in v.columns.items() if "_" in k}, **v.tables}
    names = {k: x for k, x in names.items() if "_" in k}
    if not names:
        return text
    pattern = re.compile(r"\b(" + "|".join(map(re.escape, sorted(names, key=len)[::-1])) + r")\b")
    return pattern.sub(lambda m: names[m.group(1)], text)


def rename_schema_text(text: str, v: Variant) -> str:
    out = []
    for line in text.splitlines():
        if m := re.match(r"TABLE (\w+):(.*)", line):
            line = f"TABLE {v.tables.get(m.group(1), m.group(1))}:{_rename_words(m.group(2), v)}"
        elif m := re.match(r"  (\w+) ([^:]*?)(: .*)?$", line):
            col, tags, comment = m.group(1), m.group(2), m.group(3) or ""
            tags = re.sub(
                r"-> (\w+)\.(\w+)",
                lambda f: f"-> {v.tables.get(f[1], f[1])}.{v.columns.get(f[2], f[2])}",
                tags,
            )
            line = f"  {v.columns.get(col, col)} {tags}{_rename_words(comment, v)}"
        else:  # the header
            line = _rename_words(line, v).replace(
                "row of terms ", f"row of {v.tables.get('terms', 'terms')} "
            )
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


_NOT_NAMES = {TokenType.STRING, TokenType.NUMBER, TokenType.NATIONAL_STRING}


def rename_sql(sql: str, v: Variant) -> str | None:
    """Rename tables and columns in one query, changing nothing else. None if the query
    can't be tokenized. Columns are renamed by name (the same in every table); a word
    right after EXTRACT( or before a "(" (a function) is left alone."""
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except sqlglot.errors.TokenError:
        return None
    edits = []
    for i, tok in enumerate(tokens):
        if tok.token_type in _NOT_NAMES or tok.token_type == TokenType.IDENTIFIER:
            continue  # literals; quoted identifiers are left as written
        word = tok.text.lower()
        new = v.tables.get(word) or v.columns.get(word)
        if not new:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        prev = tokens[i - 1] if i > 0 else None
        if nxt is not None and nxt.token_type == TokenType.L_PAREN:
            continue  # a function call
        if prev is not None and prev.token_type == TokenType.L_PAREN and i > 1:
            if tokens[i - 2].text.upper() == "EXTRACT":
                continue  # EXTRACT(YEAR FROM ...)
        edits.append((tok.start, tok.end + 1, new))
    for start, end, new in reversed(edits):
        sql = sql[:start] + new + sql[end:]
    return sql


def view_ddl(v: Variant, tables: dict[str, list[str]], schema: str | None) -> list[str]:
    """CREATE VIEW statements that show the real tables under the variant's names.
    schema=None makes TEMP views (they then read the per-student views of the scorer)."""
    stmts = []
    for table, cols in tables.items():
        select = ", ".join(f"{c} AS {v.columns.get(c, c)}" for c in cols)
        name = v.tables.get(table, table)
        target = f"{schema}.{name}" if schema else name
        kind = "VIEW" if schema else "TEMP VIEW"
        source = f"public.{table}" if schema else table
        stmts.append(f"CREATE {kind} {target} AS SELECT {select} FROM {source}")
    return stmts


def _rows(cur: psycopg.Cursor, sql: str) -> list[tuple] | None:
    cur.execute("SAVEPOINT q")
    try:
        cur.execute(sql)
        rows = sorted(cur.fetchall(), key=repr)
        cur.execute("RELEASE SAVEPOINT q")
        return rows
    except psycopg.Error:
        cur.execute("ROLLBACK TO SAVEPOINT q")
        return None


class VariantChecker:
    """Checks renamed queries against views with the variant's names, as the table owner,
    inside one transaction that is rolled back: nothing stays in the database."""

    def __init__(self, conn: psycopg.Connection, variants: list[Variant], schema_text: str):
        self.cur = conn.cursor()
        self.tables = parse_schema(schema_text)
        self.schemas = {}
        self.cur.execute("SELECT set_config('statement_timeout', %s, true)",
                         (get_settings().statement_timeout,))  # fmt: skip
        for i, v in enumerate(variants):
            name = f"sqlib_variant_{i}"
            self.cur.execute(f"CREATE SCHEMA {name}")
            for stmt in view_ddl(v, self.tables, name):
                self.cur.execute(stmt)
            self.schemas[v.name] = name
        self._original: dict[str, list[tuple] | None] = {}

    def same_rows(self, sql: str, renamed: str, v: Variant) -> bool:
        if sql not in self._original:
            self.cur.execute("SET LOCAL search_path TO public")
            self._original[sql] = _rows(self.cur, sql)
        expected = self._original[sql]
        if expected is None:
            return False
        self.cur.execute(f"SET LOCAL search_path TO {self.schemas[v.name]}")
        got = _rows(self.cur, renamed)
        self.cur.execute("SET LOCAL search_path TO public")
        return got == expected


def export_heldout() -> None:
    """Held-out schema text + dev questions with renamed reference SQL, each checked."""
    s = get_settings()
    schema_text = s.paths.schema.read_text("utf-8")
    questions = [json.loads(x) for x in s.paths.dev_questions.read_text("utf-8").splitlines()]
    out, failed = [], []
    with psycopg.connect(s.records_dsn) as conn:
        checker = VariantChecker(conn, [HELDOUT], schema_text)
        for q in questions:
            renamed = rename_sql(q["reference_sql"], HELDOUT)
            if renamed is None or not checker.same_rows(q["reference_sql"], renamed, HELDOUT):
                failed.append(q["id"])
            out.append({**q, "reference_sql": renamed})
        conn.rollback()
    if failed:
        raise SystemExit(f"renaming changed the answer of {failed}: fix rename_sql first")
    s.paths.schema_heldout.write_text(rename_schema_text(schema_text, HELDOUT), "utf-8")
    s.paths.dev_questions_heldout.write_text(
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in out), "utf-8"
    )
    log.info(
        "variants.heldout_exported",
        questions=len(out),
        schema=str(s.paths.schema_heldout),
        dev=str(s.paths.dev_questions_heldout),
    )


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="Renamed schema variants.")
    p.add_argument("command", choices=["export-heldout", "show"])
    p.add_argument("--n", type=int, default=get_settings().schema_variant_count)
    args = p.parse_args()
    if args.command == "export-heldout":
        export_heldout()
    else:
        text = get_settings().paths.schema.read_text("utf-8")
        seed = get_settings().schema_variant_seed
        for v in make_variants(args.n, seed, text):
            print(v.name, json.dumps(v.tables), json.dumps(v.columns))  # command output


if __name__ == "__main__":
    main()
