"""Small helpers shared by the data scripts."""

import sqlglot
from sqlglot import exp


def normalize_sql(sql: str) -> str | None:
    """Rewrite a query in one standard form, so two spellings of the same
    query compare equal. Returns None if it doesn't parse."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return None
    return tree.sql(dialect="postgres", normalize=True)


def is_single_select(sql: str) -> bool:
    """True if the text is exactly one SELECT (or UNION of SELECTs)
    with no INSERT/UPDATE/DELETE hidden inside."""
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return False
    if len(statements) != 1 or statements[0] is None:
        return False
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return False
    return not any(tree.find_all(exp.Insert, exp.Update, exp.Delete, exp.Drop))


def filters_on_student_literal(sql: str) -> bool:
    """True if the query hard-codes a student, e.g. WHERE student_id = 42.
    Training queries must be written for 'the current student' with no filter,
    because row-level security does the filtering."""
    tree = sqlglot.parse_one(sql, read="postgres")
    for eq in tree.find_all(exp.EQ):
        cols = [c.name for c in eq.find_all(exp.Column)]
        if "student_id" in cols and eq.find(exp.Literal) is not None:
            return True
    return False
