"""Renamed schema variants: the SQL and the schema text change together, and nothing else."""

import psycopg
import pytest

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.training.schema_variants import (
    COLUMN_SYNONYMS,
    HELDOUT,
    TABLE_SYNONYMS,
    Variant,
    VariantChecker,
    is_consistent,
    make_variants,
    parse_schema,
    rename_schema_text,
    rename_sql,
)

SCHEMA = get_settings().paths.schema.read_text("utf-8")
V = Variant(
    "t",
    {"students": "learners", "enrollments": "regs"},
    {"cumulative_gpa": "cgpa", "status": "state", "total": "mark", "year": "yr"},
)


def test_rename_sql_changes_names_only():
    sql = (
        "SELECT s.cumulative_gpa, e.total AS total FROM students s JOIN enrollments e USING "
        "(student_id) WHERE e.status = 'passed' AND students.cumulative_gpa > 2 ORDER BY total"
    )
    assert rename_sql(sql, V) == (
        "SELECT s.cgpa, e.mark AS mark FROM learners s JOIN regs e USING "
        "(student_id) WHERE e.state = 'passed' AND learners.cgpa > 2 ORDER BY mark"
    )


def test_rename_sql_leaves_literals_functions_and_extract_alone():
    sql = "SELECT EXTRACT(YEAR FROM now()), 'status total', year FROM terms"
    assert rename_sql(sql, V) == "SELECT EXTRACT(YEAR FROM now()), 'status total', yr FROM terms"


def test_schema_text_is_renamed_with_the_sql():
    text = rename_schema_text(SCHEMA, HELDOUT)
    assert "TABLE student_profiles:" in text and "TABLE students:" not in text
    assert "  cgpa NUMERIC:" in text
    assert "-> student_profiles.student_ref" in text
    assert "never filter by student_ref" in text
    assert "row of academic_terms where current_flag" in text
    assert set(parse_schema(text)) == set(HELDOUT.tables.values())


def test_training_variants_are_consistent_and_never_use_heldout_names():
    tables = parse_schema(SCHEMA)
    variants = make_variants(16, 7, SCHEMA)
    assert len({(tuple(v.tables.items()), tuple(v.columns.items())) for v in variants}) == 16
    assert all(is_consistent(v, tables) for v in variants)
    assert is_consistent(HELDOUT, tables)
    heldout = set(HELDOUT.tables.values()) | set(HELDOUT.columns.values())
    training = {n for names in TABLE_SYNONYMS.values() for n in names}
    training |= {n for names in COLUMN_SYNONYMS.values() for n in names}
    assert not heldout & training
    assert make_variants(4, 7, SCHEMA) == variants[:4], "same seed, same variants"


def _db_up() -> bool:
    try:
        psycopg.connect(get_settings().records_dsn, connect_timeout=2).close()
        return True
    except (psycopg.Error, SystemExit):
        return False


@pytest.mark.skipif(not _db_up(), reason="records_db not running")
def test_checker_keeps_a_correct_rename_and_catches_a_wrong_one():
    with psycopg.connect(get_settings().records_dsn) as conn:
        checker = VariantChecker(conn, [V], SCHEMA)
        sql = "SELECT count(*) FROM enrollments WHERE status = 'passed'"
        assert checker.same_rows(sql, rename_sql(sql, V), V)
        wrong = "SELECT count(*) FROM regs WHERE state = 'failed'"
        assert not checker.same_rows(sql, wrong, V)
        assert not checker.same_rows(sql, "SELECT count(*) FROM enrollments", V)
        conn.rollback()
