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

Database-agnostic (2026-11-20): a prompt can show a renamed schema (its `schema_variant`
column, training/schema_variants.py). Its completion is then run on views with that
variant's names, each limited to the current student (evaluation/db.py STUDENT_ROWS),
so the model is rewarded for reading the names in the prompt, not for remembering ours.
All views live in one transaction that is never committed: nothing stays in the database.

Needs RECORDS_DB_URL (the GRPO notebook starts Postgres and restores the fake data).
"""

import psycopg

from sqlibrarian.evaluation.db import create_schema_views, sample_students, use_schema_views
from sqlibrarian.evaluation.score import judge, students_for
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import ScoreStatus
from sqlibrarian.helpers.log import get_logger
from sqlibrarian.helpers.prompt import extract_sql
from sqlibrarian.training.schema_variants import Variant, make_variants
from sqlibrarian.training.sql_utils import filters_by_student, is_single_select

log = get_logger(__name__)

REWARD = {
    ScoreStatus.CORRECT: 1.0,
    ScoreStatus.WRONG: 0.1,
    ScoreStatus.ERROR: 0.0,
    ScoreStatus.INVALID: -0.5,
}
FILTERS_BY_STUDENT = -1.0
ORIGINAL = "original"


def training_variants() -> list[Variant]:
    """The renamed schemas push-own used (same settings, same seed: same names)."""
    s = get_settings()
    return make_variants(
        s.schema_variant_count, s.schema_variant_seed, s.paths.schema.read_text("utf-8"),
        table_share=s.schema_table_share, column_share=s.schema_column_share,
    )  # fmt: skip


class ExecutionReward:
    """Callable reward for TRL's GRPOTrainer: reward(prompts, completions, reference_sql=...).

    TRL passes every extra dataset column as a keyword argument, so the dataset only needs
    a `reference_sql` column. One database connection is kept open for the whole run, and
    each question's witness student is looked up once (cached)."""

    __name__ = "execution_reward"  # TRL logs rewards under the function's name

    def __init__(self, n_students: int, variants: list[Variant] | None = None) -> None:
        self.n_students = n_students
        self.variants = {v.name: v for v in (training_variants() if variants is None else variants)}
        self._conn: psycopg.Connection | None = None
        self._schemas: dict[str, str] = {}  # variant name -> its schema of views
        self._sample: list[int] = []
        self._witness_cache: dict[str, list[int]] = {}

    def _cursor(self) -> psycopg.Cursor:
        """One connection and one open transaction for the whole run: the per-student
        temporary views and the variant schemas are made once; every score runs inside a
        savepoint that is rolled back. The transaction is never committed."""
        if self._conn is None or self._conn.closed:
            s = get_settings()
            self._conn = psycopg.connect(s.records_dsn)
            cur = self._conn.cursor()
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (s.statement_timeout,))
            self._sample = sample_students(cur)[: self.n_students]
            # Our names get a schema too, so every prompt is scored the same way and only
            # the names of its own schema are visible.
            shown = [Variant(ORIGINAL, {}, {}), *self.variants.values()]
            for i, v in enumerate(shown):
                schema = f"sqlib_reward_{i}"
                create_schema_views(cur, schema, v)
                self._schemas[v.name] = schema
            log.info("reward.connected", students=self._sample, variants=len(self._schemas))
        return self._conn.cursor()

    def score(self, completion_text: str, reference_sql: str, variant: str = ORIGINAL) -> float:
        sql = extract_sql(completion_text)
        if sql and is_single_select(sql) and filters_by_student(sql):
            return FILTERS_BY_STUDENT
        cur = self._cursor()
        cur.execute("SAVEPOINT score")
        try:
            if variant not in self._schemas:
                raise ValueError(f"unknown schema variant {variant!r}: re-run push-own")
            use_schema_views(cur, self._schemas[variant])
            question = {"id": f"{variant}:{reference_sql}", "reference_sql": reference_sql}
            students = students_for(cur, question, self._sample, self._witness_cache)
            status, _ = judge(cur, sql, reference_sql, students)
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT score")  # nothing kept; search_path restored
        return REWARD[status]

    def __call__(self, prompts, completions, reference_sql, schema_variant=None, **kwargs):
        texts = [c[0]["content"] if isinstance(c, list) else c for c in completions]
        variants = schema_variant or [ORIGINAL] * len(texts)
        rewards = [
            self.score(t, ref, v) for t, ref, v in zip(texts, reference_sql, variants, strict=True)
        ]
        log.debug("reward.batch", n=len(rewards), mean=round(sum(rewards) / len(rewards), 3))
        return rewards
