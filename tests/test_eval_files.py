"""Checks on the hand-made evaluation files that need no database."""

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from sqlibrarian.api.guard import ALLOWED_TABLES
from sqlibrarian.training.sql_utils import filters_by_student, is_single_select

EVAL = Path(__file__).resolve().parents[1] / "data/eval"
FIELDS = {"id", "question", "lang", "difficulty", "reference_sql"}


def load(name: str) -> list[dict]:
    path = EVAL / name
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


DEV = load("dev_questions.jsonl")


def test_dev_set_shape():
    assert len(DEV) == 59  # 50 + 9 for standing, warnings, excuses, registration (schema v2)
    assert all(set(q) == FIELDS for q in DEV)
    assert len({q["id"] for q in DEV}) == len(DEV)
    assert Counter(q["lang"] for q in DEV) == {"en": 23, "ar": 18, "mixed": 18}
    assert {q["difficulty"] for q in DEV} <= {"easy", "medium", "hard"}


@pytest.mark.parametrize("q", DEV, ids=[q["id"] for q in DEV])
def test_dev_reference_is_safe_select_for_current_student(q):
    assert is_single_select(q["reference_sql"])
    assert not filters_by_student(q["reference_sql"])


def test_schema_text_lists_exactly_the_allowed_tables():
    """The model is shown every table the guard lets through, and nothing else."""
    text = (EVAL / "schema.txt").read_text("utf-8")
    assert set(re.findall(r"^TABLE (\w+):", text, re.M)) == ALLOWED_TABLES
