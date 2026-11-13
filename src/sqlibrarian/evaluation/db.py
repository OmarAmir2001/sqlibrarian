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
_VIEWS = [
    f"CREATE TEMP VIEW students AS SELECT * FROM public.students WHERE student_id = {_SID}",
    f"CREATE TEMP VIEW enrollments AS SELECT * FROM public.enrollments WHERE student_id = {_SID}",
    f"CREATE TEMP VIEW plans AS SELECT * FROM public.plans WHERE student_id = {_SID}",
    f"CREATE TEMP VIEW excuses AS SELECT * FROM public.excuses WHERE student_id = {_SID}",
    "CREATE TEMP VIEW improvement_plans AS SELECT * FROM public.improvement_plans"
    f" WHERE student_id = {_SID}",
    "CREATE TEMP VIEW plan_courses AS SELECT pc.* FROM public.plan_courses pc"
    f" JOIN public.plans p ON p.plan_id = pc.plan_id WHERE p.student_id = {_SID}",
]

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
    """Show only this student's rows from now on, until the transaction ends."""
    cur.execute("SELECT to_regclass('pg_temp.enrollments') IS NOT NULL")
    if not cur.fetchone()[0]:
        for stmt in _VIEWS:
            cur.execute(stmt)
    cur.execute("SELECT set_config('eval.student_id', %s, true)", (str(int(student_id)),))
