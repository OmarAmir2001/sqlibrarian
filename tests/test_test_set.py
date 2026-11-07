"""Test-set checks that need no database."""

from sqlibrarian.evaluation.test_set import (
    Severity,
    check_duplicates,
    check_fields,
    normalize_question,
)

COURSES = {"Data Structures", "هياكل البيانات"}
GOOD = {
    "lang": "en",
    "difficulty": "easy",
    "kind": "grade",
    "question": "my grade in data structures",
    "reference_sql": "SELECT e.letter_grade FROM enrollments e JOIN courses c "
    "ON c.course_code = e.course_code WHERE c.title_en = 'Data Structures';",
}


def test_normalize_unifies_arabic_variants_and_punctuation():
    assert normalize_question("ما هي درجتي في مادة الإحصاء؟") == normalize_question(
        "ما هى درجتى فى ماده الاحصاء"
    )
    assert normalize_question("What's my GPA?!") == normalize_question("what s my gpa")


def test_good_row_has_no_problems():
    assert check_fields(2, GOOD, COURSES) == []


def test_bad_fields_are_errors():
    row = {**GOOD, "lang": "eg", "difficulty": "trivial", "kind": "", "question": ""}
    messages = [p.message for p in check_fields(5, row, COURSES)]
    assert any("lang must be" in m for m in messages)
    assert any("difficulty must be" in m for m in messages)
    assert any("kind is empty" in m for m in messages)
    assert any("question is empty" in m for m in messages)


def test_unsafe_sql_is_an_error():
    for sql, expected in [
        ("DELETE FROM students", "not exactly one SELECT"),
        ("SELECT * FROM students WHERE student_id = 7", "filters by student"),
        (
            "SELECT 1 FROM courses c WHERE c.title_en = 'Database Systems'",
            "unknown course",
        ),
    ]:
        problems = check_fields(3, {**GOOD, "reference_sql": sql}, COURSES)
        assert problems and problems[0].severity is Severity.ERROR
        assert expected in problems[0].message


def test_duplicates_in_file_are_errors_and_dev_lookalikes_are_warnings():
    rows = [
        (2, {"question": "what is my gpa now"}),
        (3, {"question": "What is my GPA now?"}),
        (4, {"question": "which courses did i fail"}),
    ]
    problems = check_duplicates(rows, dev_questions=["Which courses did I fail?"])
    assert [(p.row, p.severity) for p in problems] == [(3, Severity.ERROR), (4, Severity.WARNING)]
