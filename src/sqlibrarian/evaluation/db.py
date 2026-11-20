"""Database helpers shared by the evaluation scripts.

Until row-level security exists (P2), "what one student sees" is imitated with
temporary views: in the current session, `students`, `enrollments`, `plans`,
`plan_courses`, `excuses` and `improvement_plans` are replaced by views that keep only
the rows of the student named in the setting `eval.student_id` (PostgreSQL looks in the
temporary schema first).
The views are created once; switching student only changes the setting, the same
way the API will set `app.student_id` for row-level security. Callers roll back
afterwards, so nothing is saved.
"""

import psycopg

_SID = "current_setting('eval.student_id')::int"
# What one student may see of each personal table: the same filter row-level security
# applies in the API. Used for the scorer's temporary views and for the GRPO reward's
# renamed-schema views (training/rewards.py), so both always agree.
STUDENT_ROWS = {
    "students": f"SELECT * FROM public.students WHERE student_id = {_SID}",
    "enrollments": f"SELECT * FROM public.enrollments WHERE student_id = {_SID}",
    "plans": f"SELECT * FROM public.plans WHERE student_id = {_SID}",
    "excuses": f"SELECT * FROM public.excuses WHERE student_id = {_SID}",
    "improvement_plans": f"SELECT * FROM public.improvement_plans WHERE student_id = {_SID}",
    "plan_courses": "SELECT pc.* FROM public.plan_courses pc"
    f" JOIN public.plans p ON p.plan_id = pc.plan_id WHERE p.student_id = {_SID}",
}
_VIEWS = [f"CREATE TEMP VIEW {table} AS {rows}" for table, rows in STUDENT_ROWS.items()]

# Five kinds of student, so a query has to be right for more than one record:
# weakest and strongest GPA, a new student with no grades, an IS student, one with a plan.
SAMPLE_STUDENTS_SQL = """
(SELECT student_id FROM public.students WHERE cumulative_gpa IS NOT NULL
 ORDER BY cumulative_gpa, student_id LIMIT 1)
UNION ALL (SELECT student_id FROM public.students
           ORDER BY cumulative_gpa DESC NULLS LAST, student_id LIMIT 1)
UNION ALL (SELECT student_id FROM public.students WHERE cumulative_gpa IS NULL
           ORDER BY student_id LIMIT 1)
UNION ALL (SELECT student_id FROM public.students WHERE program = 'IS' AND level = 3
           ORDER BY student_id LIMIT 1)
UNION ALL (SELECT s.student_id FROM public.students s JOIN public.plans p USING (student_id)
           WHERE p.status = 'approved' AND s.level = 2 ORDER BY s.student_id LIMIT 1)
"""


def sample_students(cur: psycopg.Cursor) -> list[int]:
    cur.execute(SAMPLE_STUDENTS_SQL)
    return list(dict.fromkeys(r[0] for r in cur.fetchall()))


def act_as_student(cur: psycopg.Cursor, student_id: int) -> None:
    """Show only this student's rows from now on, until the transaction ends.
    Inside use_schema_views() no temporary views are made: the schema's views already
    keep only this student's rows."""
    cur.execute(
        "SELECT to_regclass('pg_temp.enrollments') IS NOT NULL"
        " OR coalesce(current_setting('eval.schema_views', true), '') = 'on'"
    )
    if not cur.fetchone()[0]:
        for stmt in _VIEWS:
            cur.execute(stmt)
    cur.execute("SELECT set_config('eval.student_id', %s, true)", (str(int(student_id)),))


def create_schema_views(cur: psycopg.Cursor, schema: str, variant) -> None:
    """A schema holding every table under the variant's names (training/schema_variants.py),
    personal tables limited to the current student (STUDENT_ROWS). Not committed by the
    callers, so it disappears with the transaction."""
    from sqlibrarian.helpers.config import get_settings
    from sqlibrarian.training.schema_variants import parse_schema, view_ddl

    tables = parse_schema(get_settings().paths.schema.read_text("utf-8"))
    cur.execute(f"CREATE SCHEMA {schema}")
    for stmt in view_ddl(variant, tables, schema, sources=STUDENT_ROWS):
        cur.execute(stmt)


def use_schema_views(cur: psycopg.Cursor, schema: str) -> None:
    """Until the transaction (or savepoint) ends, queries see ONLY that schema's names:
    not the real tables (public is off the path) and no temporary views under our names
    (none are made, see act_as_student). pg_catalog functions stay available."""
    cur.execute("SELECT set_config('eval.schema_views', 'on', true)")
    cur.execute(f"SET LOCAL search_path TO {schema}, pg_temp")


def show_variant(cur: psycopg.Cursor, variant) -> None:
    """Show the database under a renamed variant's names only (held-out scoring).
    Before 2026-11-20 this added temporary views next to the per-student ones, so a
    query written with OUR names still ran: held-out scores up to run 9 may be too high."""
    create_schema_views(cur, "sqlib_shown", variant)
    use_schema_views(cur, "sqlib_shown")
