"""The enums must allow exactly what the database allows, and settings must load
with no .env at all (Kaggle), failing loudly only when a script needs a secret."""

import re
from pathlib import Path

import pytest

from sqlibrarian.config import Settings
from sqlibrarian.enums import EnrollmentStatus, PlanRule, PlanStatus, Program, Season, TermType

ROOT = Path(__file__).resolve().parents[1]
RECORDS_SQL = (ROOT / "db/records/01_schema.sql").read_text("utf-8")
APP_SQL = (ROOT / "db/app/01_app.sql").read_text("utf-8")


def check_values(sql: str, column: str) -> set[str]:
    """Values in `CHECK (<column> IN ('a', 'b'))` for the column's first definition."""
    match = re.search(rf"\b{column}\b[^,]*?CHECK \({column} IN \(([^)]*)\)\)", sql, re.S)
    assert match, f"no CHECK list found for {column}"
    return set(re.findall(r"'([^']*)'", match.group(1)))


@pytest.mark.parametrize(
    ("enum", "sql", "column"),
    [
        (Season, RECORDS_SQL, "season"),
        (TermType, RECORDS_SQL, "term_type"),
        (EnrollmentStatus, RECORDS_SQL, "status"),
        (PlanRule, APP_SQL, "rule"),
    ],
)
def test_enum_matches_schema_check(enum, sql, column):
    assert {m.value for m in enum} == check_values(sql, column)


def test_plan_status_matches_schema():
    plans = RECORDS_SQL[RECORDS_SQL.index("CREATE TABLE plans") :]
    assert {m.value for m in PlanStatus} == check_values(plans, "status")


def test_program_values_match_schema():
    courses = RECORDS_SQL[RECORDS_SQL.index("CREATE TABLE courses") :]
    assert {m.value for m in Program} == check_values(courses, "program")


def test_graded_statuses():
    assert set(EnrollmentStatus.graded()) == {"passed", "failed"}


def test_settings_load_without_env_file_and_fail_loudly_on_missing_db(monkeypatch):
    monkeypatch.delenv("RECORDS_DB_URL", raising=False)
    s = Settings(_env_file=None)
    assert s.base_model.startswith("Qwen/")
    assert s.paths.schema == s.project_root / "data/eval/schema.txt"
    with pytest.raises(SystemExit, match="RECORDS_DB_URL"):
        _ = s.records_dsn


def test_settings_reject_bad_values():
    with pytest.raises(ValueError):
        Settings(_env_file=None, max_train_tokens=0)
