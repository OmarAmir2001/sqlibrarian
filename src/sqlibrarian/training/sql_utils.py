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


def filters_by_student(sql: str) -> bool:
    """True if the query picks a student itself: student_id compared with anything that
    is not another column (42, :student_id, current_user_id(), a subquery), or
    student_id IN (...). Queries must be written for 'the current student' with no
    filter, because row-level security does the filtering. Joins such as
    e.student_id = s.student_id are fine."""
    tree = sqlglot.parse_one(sql, read="postgres")
    for cmp in tree.find_all(exp.EQ, exp.NEQ, exp.In):
        sides = [cmp.this] + ([cmp.expression] if not isinstance(cmp, exp.In) else [])
        touches = any(isinstance(x, exp.Column) and x.name == "student_id" for x in sides)
        if not touches:
            continue
        if isinstance(cmp, exp.In) or not all(isinstance(x, exp.Column) for x in sides):
            return True
    return False


# Old name, kept so existing callers keep working.
filters_on_student_literal = filters_by_student


def course_names_used(sql: str) -> set[str]:
    """Course names the query matches exactly, e.g. {'Data Structures'} from
    WHERE c.title_en = 'Data Structures' (or title_ar, or IN ('...', '...'))."""
    tree = sqlglot.parse_one(sql, read="postgres")
    names = set()
    for cmp in tree.find_all(exp.EQ, exp.In):
        cols = [x for x in cmp.find_all(exp.Column) if x.name in ("title_en", "title_ar")]
        if cols:
            names |= {lit.this for lit in cmp.find_all(exp.Literal) if lit.is_string}
    return names
