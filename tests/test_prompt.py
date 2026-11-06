import pytest

from sqlibrarian.helpers.prompt import SYSTEM, build_messages, extract_sql


def test_messages_without_sql_have_system_and_user_only():
    msgs = build_messages("TABLE t (a INT)", "What's my GPA?")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == SYSTEM
    assert "TABLE t (a INT)" in msgs[1]["content"]
    assert "What's my GPA?" in msgs[1]["content"]


def test_messages_with_sql_add_trimmed_assistant_answer():
    msgs = build_messages("TABLE t (a INT)", "q", sql="  SELECT 1  \n")
    assert msgs[-1] == {"role": "assistant", "content": "SELECT 1"}


def test_examples_go_between_schema_and_question():
    msgs = build_messages("TABLE t (a INT)", "my GPA?", examples=[("q1", "SELECT 1")])
    user = msgs[1]["content"]
    assert user.index("TABLE t") < user.index("Question: q1") < user.index("Question: my GPA?")
    assert "SQL: SELECT 1" in user


@pytest.mark.parametrize(
    ("reply", "sql"),
    [
        ("SELECT 1", "SELECT 1"),
        ("```sql\nSELECT a FROM t;\n```", "SELECT a FROM t"),
        ("Here is the query:\nSELECT a FROM t; DROP TABLE t", "SELECT a FROM t"),
        (
            "<think>maybe SELECT b</think>\nWITH x AS (SELECT 1) SELECT * FROM x",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ),
        ("I can't answer that.", ""),
    ],
)
def test_extract_sql(reply, sql):
    assert extract_sql(reply) == sql
