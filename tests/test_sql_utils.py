import pytest

from sqlibrarian.training.sql_utils import (
    filters_by_student,
    is_single_select,
    normalize_sql,
)


def test_normalize_makes_spellings_equal():
    assert normalize_sql("select a from t") == normalize_sql("SELECT   a\nFROM t")


def test_normalize_returns_none_for_garbage():
    assert normalize_sql("SELECT FROM WHERE (") is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT grade FROM enrollments",
        "SELECT a FROM t UNION SELECT a FROM u",
    ],
)
def test_single_select_accepted(sql):
    assert is_single_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; SELECT 2",
        "DELETE FROM students",
        "UPDATE plans SET status = 'approved'",
        "WITH x AS (DELETE FROM students RETURNING *) SELECT * FROM x",
        "DROP TABLE students",
        "not sql at all (",
    ],
)
def test_unsafe_or_multiple_statements_rejected(sql):
    assert not is_single_select(sql)


def test_hard_coded_student_detected():
    assert filters_by_student("SELECT * FROM enrollments WHERE student_id = 42")


def test_query_for_current_student_allowed():
    assert not filters_by_student("SELECT grade FROM enrollments WHERE term_id = 3")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM enrollments WHERE student_id = :student_id",
        "SELECT * FROM enrollments WHERE student_id = current_user_id()",
        "SELECT * FROM enrollments WHERE student_id = (SELECT MIN(student_id) FROM students)",
        "SELECT * FROM enrollments WHERE student_id IN (1, 2)",
        "SELECT * FROM enrollments e WHERE e.student_id <> 7",
    ],
)
def test_any_student_filter_detected(sql):
    assert filters_by_student(sql)


def test_join_on_student_id_allowed():
    sql = (
        "SELECT s.name_en, e.total FROM students s "
        "JOIN enrollments e ON e.student_id = s.student_id"
    )
    assert not filters_by_student(sql)


def test_course_names_used():
    from sqlibrarian.training.sql_utils import course_names_used

    sql = (
        "SELECT e.total FROM enrollments e JOIN courses c ON c.course_code = e.course_code "
        "WHERE c.title_en = 'Data Structures' OR c.title_ar IN ('هياكل البيانات', 'نظم التشغيل') "
        "AND e.status = 'passed'"
    )
    assert course_names_used(sql) == {"Data Structures", "هياكل البيانات", "نظم التشغيل"}
