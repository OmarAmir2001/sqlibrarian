"""Write data/eval/schema.txt: the description of records_db that the model reads.

It is built from the live database, so it always matches what queries run against:
table and column names, types, keys, and the COMMENT on every table and column
(written in db/records/01_schema.sql; "-" = no text needed). The same text goes into
training, evaluation and the API prompt, so change the comments in the .sql file, not
this output.

Run (records_db up):
    uv run python db/export_schema.py
"""

import psycopg

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

HEADER = (
    "PostgreSQL database of one university student's records.\n"
    "Rows are already limited to the student asking: never filter by student_id.\n"
    "The current term is the row of terms where is_current is true."
)

# Short type names keep the text small; the model only needs the kind of value.
TYPES = {
    "integer": "INT",
    "bigint": "INT",
    "text": "TEXT",
    "boolean": "BOOL",
    "timestamp with time zone": "TIMESTAMP",
}

TABLES_SQL = """
SELECT c.oid, c.relname, obj_description(c.oid, 'pg_class')
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r'
ORDER BY c.oid
"""

COLUMNS_SQL = """
SELECT a.attname, format_type(a.atttypid, a.atttypmod), col_description(a.attrelid, a.attnum),
       EXISTS (SELECT 1 FROM pg_constraint k
               WHERE k.conrelid = a.attrelid AND k.contype = 'p' AND a.attnum = ANY (k.conkey))
FROM pg_attribute a
WHERE a.attrelid = %s AND a.attnum > 0 AND NOT a.attisdropped
ORDER BY a.attnum
"""

# Single-column foreign keys: (column) -> other_table.other_column
FKEYS_SQL = """
SELECT a.attname, f.relname, fa.attname
FROM pg_constraint k
JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = k.conkey[1]
JOIN pg_class f ON f.oid = k.confrelid
JOIN pg_attribute fa ON fa.attrelid = k.confrelid AND fa.attnum = k.confkey[1]
WHERE k.conrelid = %s AND k.contype = 'f' AND cardinality(k.conkey) = 1
"""


def short_type(pg_type: str) -> str:
    if pg_type.startswith("numeric"):
        return "NUMERIC"
    return TYPES.get(pg_type, pg_type.upper())


def build_schema_text(conn: psycopg.Connection) -> str:
    lines = [HEADER, ""]
    with conn.cursor() as cur:
        cur.execute(TABLES_SQL)
        for oid, table, table_comment in cur.fetchall():
            cur.execute(FKEYS_SQL, (oid,))
            fkeys = {col: f"{t}.{c}" for col, t, c in cur.fetchall()}
            cur.execute(COLUMNS_SQL, (oid,))
            columns = cur.fetchall()
            missing = [name for name, _, comment, _ in columns if not comment]
            if not table_comment or missing:
                raise SystemExit(f"{table}: add a COMMENT for the table/columns {missing}")

            lines.append(f"TABLE {table}: {table_comment}")
            for name, pg_type, comment, is_pk in columns:
                tags = [short_type(pg_type)]
                if is_pk:
                    tags.append("PK")
                if name in fkeys:
                    tags.append(f"-> {fkeys[name]}")
                # A comment of "-" marks a self-explanatory column (row ids, the student):
                # it is listed without text, which keeps the prompt short.
                text = "" if comment.strip() == "-" else f": {comment}"
                lines.append(f"  {name} {' '.join(tags)}{text}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    configure_logging()
    out = get_settings().paths.schema
    with psycopg.connect(get_settings().records_dsn) as conn:
        text = build_schema_text(conn)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    log.info("schema.exported", path=str(out), lines=len(text.splitlines()), characters=len(text))


if __name__ == "__main__":
    main()
