"""Step 7.2: the GRPO reward, by running each generated query against records_db.

GRPO samples several answers per question and pushes the model toward the better ones.
"Better" here is decided by the same judge the evaluation uses (evaluation/score.py),
so training optimises exactly what is measured:

    correct (same rows as the reference for every sample student)   1.0
    runs but returns different rows                                  0.1
    fails to run (bad column, syntax, timeout)                       0.0
    not a single SELECT                                             -0.5
    filters by student itself (the baseline's top failure)          -1.0

The small reward for a query that runs keeps learning moving early on, when few
answers are fully correct; the negative ones teach the safety rules directly.

Needs RECORDS_DB_URL (the GRPO notebook starts Postgres and restores the fake data).
"""

import psycopg

from sqlibrarian.evaluation.db import sample_students
from sqlibrarian.evaluation.score import judge, students_for
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import ScoreStatus
from sqlibrarian.helpers.log import get_logger
from sqlibrarian.helpers.prompt import extract_sql
from sqlibrarian.training.sql_utils import filters_by_student, is_single_select

log = get_logger(__name__)

REWARD = {
    ScoreStatus.CORRECT: 1.0,
    ScoreStatus.WRONG: 0.1,
    ScoreStatus.ERROR: 0.0,
    ScoreStatus.INVALID: -0.5,
}
FILTERS_BY_STUDENT = -1.0


class ExecutionReward:
    """Callable reward for TRL's GRPOTrainer: reward(prompts, completions, reference_sql=...).

    TRL passes every extra dataset column as a keyword argument, so the dataset only needs
    a `reference_sql` column. One database connection is kept open for the whole run, and
    each question's witness student is looked up once (cached)."""

    __name__ = "execution_reward"  # TRL logs rewards under the function's name

    def __init__(self, n_students: int) -> None:
        self.n_students = n_students
        self._conn: psycopg.Connection | None = None
        self._sample: list[int] = []
        self._witness_cache: dict[str, list[int]] = {}

    def _cursor(self) -> psycopg.Cursor:
        if self._conn is None or self._conn.closed:
            s = get_settings()
            self._conn = psycopg.connect(s.records_dsn)
            cur = self._conn.cursor()
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (s.statement_timeout,))
            self._sample = sample_students(cur)[: self.n_students]
            log.info("reward.connected", students=self._sample)
        return self._conn.cursor()

    def score(self, completion_text: str, reference_sql: str) -> float:
        sql = extract_sql(completion_text)
        if sql and is_single_select(sql) and filters_by_student(sql):
            return FILTERS_BY_STUDENT
        cur = self._cursor()
        question = {"id": reference_sql, "reference_sql": reference_sql}
        students = students_for(cur, question, self._sample, self._witness_cache)
        status, _ = judge(cur, sql, reference_sql, students)
        self._conn.rollback()  # nothing is ever kept; also ends the temp-view transaction
        return REWARD[status]

    def __call__(self, prompts, completions, reference_sql, **kwargs) -> list[float]:
        texts = [c[0]["content"] if isinstance(c, list) else c for c in completions]
        rewards = [self.score(t, ref) for t, ref in zip(texts, reference_sql, strict=True)]
        log.debug("reward.batch", n=len(rewards), mean=round(sum(rewards) / len(rewards), 3))
        return rewards
