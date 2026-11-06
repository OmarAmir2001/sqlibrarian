import pytest

from sqlibrarian.training.sql_utils import (
    filters_on_student_literal,
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
    assert filters_on_student_literal("SELECT * FROM enrollments WHERE student_id = 42")


def test_query_for_current_student_allowed():
    assert not filters_on_student_literal("SELECT grade FROM enrollments WHERE term_id = 3")
