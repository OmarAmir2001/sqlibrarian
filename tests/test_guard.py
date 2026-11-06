"""The SQL guard: every legitimate query passes, every attack is refused."""

import json
from pathlib import Path

import pytest

from sqlibrarian.api.guard import check

ROOT = Path(__file__).resolve().parents[1]
DEV = [
    json.loads(x) for x in (ROOT / "data/eval/dev_questions.jsonl").read_text("utf-8").splitlines()
]


@pytest.mark.parametrize("q", DEV, ids=[q["id"] for q in DEV])
def test_every_dev_reference_passes(q):
    assert check(q["reference_sql"]).ok


ATTACKS = {
    "delete": "DELETE FROM students",
    "update": "UPDATE plans SET status = 'approved'",
    "drop": "DROP TABLE students",
    "two statements": "SELECT 1; DELETE FROM students",
    "delete in a CTE": "WITH x AS (DELETE FROM students RETURNING *) SELECT * FROM x",
    "insert in a CTE": "WITH x AS (INSERT INTO tas VALUES (99, 'x') RETURNING *) SELECT * FROM x",
    "select into": "SELECT * INTO stolen FROM students",
    "row lock": "SELECT * FROM plans FOR UPDATE",
    "sleep": "SELECT pg_sleep(10)",
    "change the student": "SELECT set_config('app.student_id', '1', true)",
    "read a server file": "SELECT pg_read_file('/etc/passwd')",
    "remote db": "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)",
    "system catalog": "SELECT * FROM pg_catalog.pg_roles",
    "information schema": "SELECT table_name FROM information_schema.tables",
    "unknown table": "SELECT * FROM pg_shadow",
    "union with a catalog": "SELECT name FROM tas UNION SELECT rolname FROM pg_roles",
    "copy": "COPY students TO '/tmp/x'",
    "set": "SET app.student_id = '1'",
    "transaction": "BEGIN",
    "not sql": "show me everyone's grades",
    "empty": "",
    "current_setting": "SELECT current_setting('app.student_id')",
    "lo_import": "SELECT lo_import('/etc/passwd')",
}


@pytest.mark.parametrize("sql", ATTACKS.values(), ids=ATTACKS.keys())
def test_attacks_are_refused(sql):
    result = check(sql)
    assert not result.ok and result.reason


def test_limit_is_added():
    assert check("SELECT name FROM tas").sql.endswith("LIMIT 200")


def test_a_big_limit_is_lowered_and_a_small_one_kept():
    assert check("SELECT name FROM tas LIMIT 100000").sql.endswith("LIMIT 200")
    assert check("SELECT name FROM tas LIMIT 5").sql.endswith("LIMIT 5")


def test_union_is_limited_as_a_whole():
    sql = check("SELECT name FROM tas UNION SELECT title_en FROM courses").sql
    assert sql.startswith("SELECT * FROM (") and sql.endswith("LIMIT 200")


def test_runs_the_regenerated_text_not_the_original():
    result = check("select   name /* hi */ from tas -- bye")
    assert result.ok and "/*" not in result.sql and "--" not in result.sql
