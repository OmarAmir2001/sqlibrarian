import json

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


def test_generator_rotation_covers_every_type_in_one_cycle():
    from sqlibrarian.training.generate_pairs import QUESTION_TYPES, rotation

    combos = rotation()
    # LANGUAGES lists English twice on purpose (two thirds English), so 3 x 3 per type
    assert len(combos) == len(QUESTION_TYPES) * 3 * 3
    assert {c[0] for c in combos[: len(QUESTION_TYPES)]} == set(QUESTION_TYPES)


def test_rewrites_follow_their_source_pair_by_sql(tmp_path):
    """Pair ids come from the SQL, so a rewrite finds its source again after re-validation,
    and is dropped when the source no longer passes."""
    from sqlibrarian.training.validate_pairs import pair_id, sync_rewrites

    kept_sql = "SELECT cumulative_gpa FROM students"
    key = normalize_sql(kept_sql)
    assert pair_id(key) == pair_id(normalize_sql("select  cumulative_gpa from students"))
    path = tmp_path / "rewrites.jsonl"
    rows = [
        {"id": "own-00007-eg", "source_id": "own-00007", "lang": "eg",
         "question": "معدلي كام؟", "reference_sql": kept_sql},
        {"id": "own-00008-mixed", "source_id": "own-00008", "lang": "mixed",
         "question": "gone", "reference_sql": "SELECT 1 FROM terms"},
    ]  # fmt: skip
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    sync_rewrites(path, {key: pair_id(key)})
    out = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    assert out == [{**rows[0], "id": f"{pair_id(key)}-eg", "source_id": pair_id(key)}]
