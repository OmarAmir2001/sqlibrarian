"""The GRPO reward against the real (fake-data) records_db; skipped when it isn't running."""

import json
from pathlib import Path

import psycopg
import pytest

from sqlibrarian.helpers.config import get_settings


def _db_up() -> bool:
    url = get_settings().records_db_url
    if not url:
        return False
    try:
        psycopg.connect(url, connect_timeout=2).close()
        return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="records_db is not running")

DEV = Path(__file__).resolve().parents[1] / "data/eval/dev_questions.jsonl"


def test_reward_ranks_answers_as_designed():
    from sqlibrarian.training.rewards import ExecutionReward

    ref = json.loads(DEV.read_text("utf-8").splitlines()[1])["reference_sql"]
    answers = [
        f"```sql\n{ref};\n```",  # correct, wrapped the way models often answer
        "SELECT e.total FROM enrollments e",  # runs, wrong rows
        "SELECT nope FROM students",  # fails
        "DELETE FROM students",  # not a SELECT
        "SELECT * FROM enrollments WHERE student_id = :student_id",  # filters by student
    ]
    completions = [[{"role": "assistant", "content": a}] for a in answers]
    rewards = ExecutionReward(n_students=3)(
        prompts=[None] * len(answers), completions=completions, reference_sql=[ref] * len(answers)
    )
    assert rewards == [1.0, 0.1, 0.0, -0.5, -1.0]


def test_renamed_prompts_reward_only_the_renamed_names():
    """A renamed prompt's answer runs on views with the variant's names only: the right
    query in those names scores 1.0, and the same query in OUR names is not correct
    (before 2026-11-20 our names still ran, through the per-student temporary views)."""
    from sqlibrarian.training.rewards import ExecutionReward, training_variants
    from sqlibrarian.training.schema_variants import rename_sql

    v = next(v for v in training_variants() if "students" in v.tables)
    ours = "SELECT cumulative_gpa FROM students"
    renamed = rename_sql(ours, v)
    assert renamed != ours
    reward = ExecutionReward(n_students=3, variants=[v])
    completions = [[{"role": "assistant", "content": s}] for s in (renamed, ours, ours)]
    got = reward(
        prompts=[None] * 3, completions=completions, reference_sql=[renamed, renamed, ours],
        schema_variant=[v.name, v.name, "original"],
    )  # fmt: skip
    assert got == [1.0, 0.0, 1.0]


def test_heldout_scoring_sees_only_heldout_names():
    """The held-out score must not count a query written with our names."""
    from sqlibrarian.evaluation.db import sample_students, show_variant
    from sqlibrarian.evaluation.score import judge
    from sqlibrarian.training.schema_variants import HELDOUT, rename_sql

    ours = "SELECT cumulative_gpa FROM students"
    with psycopg.connect(get_settings().records_dsn) as conn, conn.cursor() as cur:
        students = sample_students(cur)[:3]
        show_variant(cur, HELDOUT)
        assert (
            judge(cur, rename_sql(ours, HELDOUT), rename_sql(ours, HELDOUT), students)[0]
            == "correct"
        )
        assert judge(cur, ours, rename_sql(ours, HELDOUT), students)[0] == "error"
        conn.rollback()
