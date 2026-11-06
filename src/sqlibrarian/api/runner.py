"""Runs a guarded query as the logged-in user, under row-level security.

Every query runs in its own READ ONLY transaction, as the sqlib_reader role, with the
user's id set *locally* (it disappears when the transaction ends) and the statement
timeout set. Row-level security in Postgres does the filtering; this code never adds
a WHERE clause of its own.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import psycopg
from psycopg_pool import AsyncConnectionPool

from sqlibrarian.api.identity import Identity
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Role


class QueryError(Exception):
    """The database rejected the query; the message may go back to the model once."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list]


def _plain(value):
    """JSON-friendly values for the response."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value


async def run(pool: AsyncConnectionPool, sql: str, identity: Identity) -> QueryResult:
    setting, value = (
        ("app.student_id", identity.student_id)
        if identity.role is Role.STUDENT
        else ("app.ta_id", identity.ta_id)
    )
    if value is None:
        raise QueryError("this account is not linked to a student or a TA")
    try:
        async with pool.connection() as conn, conn.transaction():
            await conn.execute("SET TRANSACTION READ ONLY")
            await conn.execute(
                "SELECT set_config('statement_timeout', %s, true), set_config(%s, %s, true)",
                (get_settings().statement_timeout, setting, str(value)),
            )
            cur = await conn.execute(sql)
            columns = [d.name for d in cur.description or []]
            rows = [[_plain(v) for v in r] for r in await cur.fetchall()]
    except psycopg.Error as e:
        raise QueryError(e.diag.message_primary or type(e).__name__) from e
    return QueryResult(columns=columns, rows=rows)
