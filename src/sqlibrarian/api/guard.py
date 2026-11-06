"""The SQL guard (SDD): only harmless reads of allowed tables ever reach the database.

Row-level security is what keeps students apart; the guard is the second wall. It
stops everything that isn't a plain read (writes, locks, functions that touch the
server, other schemas) and caps the result size. It returns a *re-generated* query:
what runs is sqlglot's rendering of the checked tree, never the model's original text,
so nothing hidden in odd formatting or comments gets through.

Rules, in order:
  1. exactly one statement, and it is a SELECT or a UNION / INTERSECT / EXCEPT of SELECTs
  2. no INSERT / UPDATE / DELETE / DDL / COPY anywhere in the tree (including CTEs),
     no SELECT ... INTO, no FOR UPDATE / FOR SHARE
  3. every table is one of the records tables (no pg_catalog, information_schema, ...)
  4. every function is on the allowlist (no pg_sleep, set_config, pg_read_file, dblink, ...)
  5. LIMIT is added, or lowered to the cap (settings.result_row_limit, 200)
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from sqlibrarian.helpers.config import get_settings

ALLOWED_TABLES = frozenset({
    "terms", "courses", "prerequisites", "credit_rules", "grading_scale",
    "tas", "students", "enrollments", "plans", "plan_courses",
})  # fmt: skip

# Functions a question about student records can need. Anything else is refused.
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "bool_and", "bool_or", "string_agg", "array_agg",
    # numbers
    "round", "ceil", "ceiling", "floor", "abs", "greatest", "least", "trunc",
    # text
    "lower", "upper", "length", "trim", "btrim", "ltrim", "rtrim", "concat", "substring",
    "replace", "split_part", "left", "right", "position", "initcap",
    # conditionals, casts and predicates (sqlglot models CASE WHEN as if(), EXISTS as exists())
    "coalesce", "nullif", "cast", "case", "if", "exists", "in", "between",
    # dates
    "extract", "date_part", "current_date", "now", "date_trunc", "age",
    # window functions
    "row_number", "rank", "dense_rank", "lag", "lead", "first_value", "last_value",
})  # fmt: skip

# sqlglot node types for statements that change something.
FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Copy, exp.Into, exp.Lock, exp.Grant,
    exp.Transaction, exp.Commit, exp.Rollback, exp.Set,
)  # fmt: skip
QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str = ""  # the re-generated, limited query (only when ok)
    reason: str = ""  # why it was refused (only when not ok)


def _refuse(reason: str) -> GuardResult:
    return GuardResult(ok=False, reason=reason)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def check(sql: str) -> GuardResult:
    if not sql or not sql.strip():
        return _refuse("empty query")
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError:  # parse and tokenize errors alike
        return _refuse("the query does not parse")
    if len(statements) != 1:
        return _refuse(f"exactly one statement allowed, got {len(statements)}")
    tree = statements[0]
    if not isinstance(tree, QUERY_ROOTS):
        return _refuse(f"only SELECT queries are allowed, not {tree.key.upper()}")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _refuse(f"{node.key.upper()} is not allowed")
    if tree.find(exp.Lock) or "for update" in tree.sql(dialect="postgres").lower():
        return _refuse("row locks are not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.db or table.catalog:
            return _refuse(f"schema-qualified table {table.sql()} is not allowed")
        if name not in ALLOWED_TABLES and name not in cte_names:
            return _refuse(f"table {name!r} is not allowed")

    for func in tree.find_all(exp.Func):
        if isinstance(func, exp.AggFunc | exp.Cast | exp.Case | exp.Coalesce | exp.Round):
            continue
        name = _function_name(func)
        if name not in ALLOWED_FUNCTIONS and not isinstance(func, exp.Binary):
            return _refuse(f"function {name}() is not allowed")

    cap = get_settings().result_row_limit
    limit = tree.args.get("limit")
    current = None
    if limit is not None and isinstance(limit.expression, exp.Literal):
        current = int(limit.expression.this) if limit.expression.is_int else None
    if current is None or current > cap:
        tree = tree.limit(cap, copy=False) if isinstance(tree, exp.Select) else (
            exp.select("*").from_(tree.subquery("q")).limit(cap)
        )  # fmt: skip
    return GuardResult(ok=True, sql=tree.sql(dialect="postgres", comments=False))
