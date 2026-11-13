"""One question, start to finish (UC1/UC2):

    question -> build_messages -> model -> extract_sql -> guard -> run under RLS
             -> (one retry with the database error) -> bilingual answer -> query_log

The model only ever sees the schema text and the question. Rows never go back to the
model and are never logged: query_log stores the question, the SQL, the verdict, the
row count and the timings.
"""

import time
from dataclasses import dataclass, field
from functools import cached_property

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from sqlibrarian.api import guard, runner
from sqlibrarian.api.composer import answer_language, compose
from sqlibrarian.api.generator import Generator, ModelError
from sqlibrarian.api.identity import Identity
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import AskOutcome
from sqlibrarian.helpers.log import get_logger
from sqlibrarian.helpers.prompt import build_messages, build_retry_messages, extract_sql

log = get_logger(__name__)


@dataclass
class AskResult:
    answer: str
    outcome: AskOutcome
    sql: str | None = None
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)
    retried: bool = False
    guard_reason: str = ""


class Orchestrator:
    def __init__(
        self, generator: Generator, reader_pool: AsyncConnectionPool, app_pool: AsyncConnectionPool
    ) -> None:
        self.generator = generator
        self.reader_pool = reader_pool
        self.app_pool = app_pool

    @cached_property
    def schema_text(self) -> str:
        return get_settings().paths.schema.read_text("utf-8")

    async def ask(self, question: str, identity: Identity) -> AskResult:
        timings: dict[str, int] = {}
        result = await self._answer(question, identity, timings)
        result.timings_ms = timings
        await self._log(question, identity, result)
        log.info(
            "ask.done", user=identity.user_id, outcome=result.outcome, rows=len(result.rows),
            retried=result.retried, **{f"ms_{k}": v for k, v in timings.items()},
        )  # fmt: skip
        return result

    async def _answer(self, question: str, identity: Identity, timings: dict) -> AskResult:
        messages = build_messages(self.schema_text, question)
        retried = False
        for attempt in range(2):  # the first try and at most one retry
            try:
                with _timer(timings, "generate" if attempt == 0 else "generate_retry"):
                    sql = extract_sql(await self.generator.complete(messages))
            except ModelError as e:
                log.warning("ask.model_error", error=str(e))
                return self._done(question, AskOutcome.MODEL_ERROR, retried=retried)
            with _timer(timings, "guard"):
                verdict = guard.check(sql)
            if not verdict.ok:
                return self._done(
                    question, AskOutcome.REJECTED, sql=sql or None, retried=retried,
                    guard_reason=verdict.reason,
                )  # fmt: skip
            try:
                with _timer(timings, "run" if attempt == 0 else "run_retry"):
                    data = await runner.run(self.reader_pool, verdict.sql, identity)
            except runner.QueryError as e:
                if attempt == 0:
                    messages, retried = build_retry_messages(messages, verdict.sql, str(e)), True
                    continue
                return self._done(question, AskOutcome.DB_ERROR, sql=verdict.sql, retried=True)
            outcome = AskOutcome.ANSWERED if data.rows else AskOutcome.NO_ROWS
            return self._done(
                question, outcome, sql=verdict.sql, columns=data.columns, rows=data.rows,
                retried=retried,
            )  # fmt: skip
        raise AssertionError("unreachable")

    @staticmethod
    def _done(question: str, outcome: AskOutcome, **kw) -> AskResult:
        answer = compose(question, outcome, kw.get("columns", []), kw.get("rows", []))
        return AskResult(answer=answer, outcome=outcome, **kw)

    async def _log(self, question: str, identity: Identity, r: AskResult) -> None:
        """query_log: never the rows."""
        verdict = None
        if r.outcome is AskOutcome.REJECTED:
            verdict = "rejected"
        elif r.sql:
            verdict = "allowed"
        lang = answer_language(question)
        async with self.app_pool.connection() as conn:
            await conn.execute(
                "INSERT INTO query_log (user_id, question, language, generated_sql, guard_verdict,"
                " guard_reason, row_count, retried, timings_ms, model_version, outcome)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (identity.user_id, question, lang, r.sql, verdict, r.guard_reason or None,
                 len(r.rows), r.retried, Jsonb(r.timings_ms),
                 get_settings().served_model_name, r.outcome),
            )  # fmt: skip


class _timer:
    def __init__(self, timings: dict, name: str) -> None:
        self.timings, self.name = timings, name

    def __enter__(self):
        self.start = time.perf_counter()

    def __exit__(self, *exc):
        self.timings[self.name] = round((time.perf_counter() - self.start) * 1000)
        return False
