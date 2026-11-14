import json

import pytest

from sqlibrarian.training.sql_utils import (
    arabic_output,
    filters_by_student,
    is_single_select,
    normalize_sql,
    picks_newest_term,
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
    source = {"id": pair_id(key), "reference_sql": kept_sql}
    sync_rewrites(path, {key: source})
    out = [json.loads(x) for x in path.read_text("utf-8").splitlines()]
    assert out == [{**rows[0], "id": f"{pair_id(key)}-eg", "source_id": pair_id(key),
                    "source_sql": kept_sql}]  # fmt: skip


def test_rewrites_get_their_language_and_keep_the_link(tmp_path):
    """A rewrite's SQL is put in its language, and it still finds its source next time."""
    from sqlibrarian.training.validate_pairs import sync_rewrites

    english = "SELECT c.title_en FROM courses c"
    source = {"id": "own-a", "reference_sql": english}
    path = tmp_path / "rewrites.jsonl"
    row = {"id": "x", "lang": "eg", "question": "q", "reference_sql": english}
    path.write_text(json.dumps(row) + "\n", "utf-8")
    for _ in range(2):  # the second run links by source_sql
        sync_rewrites(path, {normalize_sql(english): source}, lambda s, _: arabic_output(s))
        out = json.loads(path.read_text("utf-8"))
        assert out["reference_sql"] == "SELECT c.title_ar FROM courses c"
        assert out["source_sql"] == english and out["source_id"] == "own-a"


def test_arabic_output_changes_shown_names_only():
    where = " WHERE c.title_en = 'Data Structures' ORDER BY c."
    assert arabic_output(f"SELECT c.title_en FROM courses c{where}title_en") == (
        f"SELECT c.title_ar FROM courses c{where}title_ar"
    )
    assert arabic_output("SELECT name_en FROM students") == "SELECT name_ar FROM students"
    same = [
        "SELECT c.title_en, c.title_ar FROM courses c",  # Arabic already shown
        "SELECT 'title_en' FROM courses WHERE title_en ILIKE '%data%'",  # literal + lookup
        "SELECT course_code FROM courses WHERE title_en IN ('Calculus 1')",
    ]
    for sql in same:
        assert arabic_output(sql) == sql


def test_picks_newest_term_flags_last_term_counted_from_the_newest():
    flagged = [
        "SELECT x FROM e WHERE term_id = (SELECT term_id FROM terms ORDER BY term_id DESC LIMIT 1)",
        "SELECT x FROM e WHERE term_id = (SELECT MAX(term_id) FROM enrollments)",
        "SELECT x FROM e WHERE term_id = (SELECT MAX(term_id) FROM terms)",
    ]
    fine = [
        "SELECT MAX(term_id) FROM terms WHERE term_id < (SELECT 7)",
        "SELECT MAX(term_id) FROM enrollments WHERE status IN ('passed', 'failed')",
        "SELECT t.year, t.season FROM terms t ORDER BY t.term_id DESC",  # a sorted list
    ]
    assert all(picks_newest_term(q) for q in flagged)
    assert not any(picks_newest_term(q) for q in fine)


def test_no_dev_reference_counts_from_the_newest_term():
    rows = [json.loads(x) for x in open("data/eval/dev_questions.jsonl", encoding="utf-8")]
    assert [r["id"] for r in rows if picks_newest_term(r["reference_sql"])] == []
