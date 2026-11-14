"""Small helpers shared by the data scripts."""

import sqlglot
from sqlglot import exp
from sqlglot.tokens import TokenType


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


# English output column -> its Arabic twin (same table).
ARABIC_TWINS = {"title_en": "title_ar", "name_en": "name_ar"}
_COMPARE = {
    TokenType.EQ, TokenType.NEQ, TokenType.IN, TokenType.LIKE, TokenType.ILIKE,
    TokenType.NOT, TokenType.IS, TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE,
}  # fmt: skip


def arabic_output(sql: str) -> str:
    """The same query, showing Arabic names: title_en -> title_ar, name_en -> name_ar,
    except where the column is compared with a value (WHERE c.title_en = 'Data Structures'
    still finds the course by the name the student typed). ORDER BY / GROUP BY follow the
    shown column. A query that already shows the Arabic column is left alone.
    Only names change; the caller checks that the new query still runs."""
    try:
        tokens = sqlglot.tokenize(sql, read="postgres")
    except sqlglot.errors.TokenError:
        return sql
    words = [t.text.lower() if t.token_type == TokenType.VAR else None for t in tokens]
    edits = []
    for i, word in enumerate(words):
        twin = ARABIC_TWINS.get(word or "")
        if not twin:
            continue
        nxt = tokens[i + 1].token_type if i + 1 < len(tokens) else None
        prev = tokens[i - 1].token_type if i > 0 else None
        if nxt in _COMPARE or prev in _COMPARE:
            continue  # used to find a row, not shown
        if twin in words:
            continue  # the Arabic column is already there
        edits.append((tokens[i].start, tokens[i].end + 1, twin))
    for start, end, new in reversed(edits):
        sql = sql[:start] + new + sql[end:]
    return sql


def picks_newest_term(sql: str) -> bool:
    """True if some part of the query picks a term by counting from the newest one:
    `terms` sorted by term_id DESC with a LIMIT, or MAX(term_id) of `terms`, without
    looking at is_current or comparing term_id; or MAX(term_id) of all `enrollments`
    (no WHERE). The newest term in `terms` is next term (no grades), and the newest in
    all enrollments is this term (no grades), so "last term" written this way is wrong.
    WHERE status IN ('passed', 'failed') makes the enrollments form right."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return False
    for sel in tree.find_all(exp.Select):
        source = sel.args.get("from_")
        table = source.this if source is not None else None
        if not isinstance(table, exp.Table) or sel.args.get("joins"):
            continue
        where = sel.args.get("where")
        takes_max = any(
            isinstance(e.unalias(), exp.Max) and e.unalias().this.name == "term_id"
            for e in sel.expressions
        )
        order = sel.args.get("order")
        newest_first = bool(
            order
            and sel.args.get("limit")
            and order.expressions[0].this.name == "term_id"
            and order.expressions[0].args.get("desc")
        )
        if table.name == "terms" and (takes_max or newest_first):
            cols = {c.name for c in where.find_all(exp.Column)} if where else set()
            if not cols & {"is_current", "term_id"}:
                return True
        if table.name == "enrollments" and takes_max and where is None:
            return True
    return False
