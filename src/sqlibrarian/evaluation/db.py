"""Database helpers shared by the evaluation scripts.

Until row-level security exists (P2), "what one student sees" is imitated with
temporary views: in the current session, `students`, `enrollments`, `plans` and
`plan_courses` are replaced by copies that hold only that student's rows
(PostgreSQL looks in the temporary schema first). Callers roll back afterwards.
"""

import psycopg

_VIEWS = [
    "CREATE TEMP VIEW students AS SELECT * FROM public.students WHERE student_id = {sid}",
    "CREATE TEMP VIEW enrollments AS SELECT * FROM public.enrollments WHERE student_id = {sid}",
    "CREATE TEMP VIEW plans AS SELECT * FROM public.plans WHERE student_id = {sid}",
    "CREATE TEMP VIEW plan_courses AS SELECT pc.* FROM public.plan_courses pc"
    " JOIN public.plans p ON p.plan_id = pc.plan_id WHERE p.student_id = {sid}",
]
_TABLES = ["plan_courses", "plans", "enrollments", "students"]

# Five kinds of student, so a query has to be right for more than one record:
# weakest and strongest GPA, a new student with no grades, an IS student, one with a plan.
SAMPLE_STUDENTS_SQL = """
(SELECT student_id FROM students WHERE cumulative_gpa IS NOT NULL ORDER BY cumulative_gpa, student_id LIMIT 1)
UNION ALL (SELECT student_id FROM students ORDER BY cumulative_gpa DESC NULLS LAST, student_id LIMIT 1)
UNION ALL (SELECT student_id FROM students WHERE cumulative_gpa IS NULL ORDER BY student_id LIMIT 1)
UNION ALL (SELECT student_id FROM students WHERE program = 'IS' AND level = 3 ORDER BY student_id LIMIT 1)
UNION ALL (SELECT s.student_id FROM students s JOIN plans p USING (student_id)
           WHERE p.status = 'approved' AND s.level = 2 ORDER BY s.student_id LIMIT 1)
"""  # noqa: E501


def sample_students(cur: psycopg.Cursor) -> list[int]:
    cur.execute(SAMPLE_STUDENTS_SQL)
    return list(dict.fromkeys(r[0] for r in cur.fetchall()))


def act_as_student(cur: psycopg.Cursor, student_id: int) -> None:
    """Show only this student's rows for the rest of the transaction."""
    for table in _TABLES:
        cur.execute(f"DROP VIEW IF EXISTS pg_temp.{table}")
    for stmt in _VIEWS:
        cur.execute(stmt.format(sid=int(student_id)))
