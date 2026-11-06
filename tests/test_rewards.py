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
