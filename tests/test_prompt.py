from sqlibrarian.prompt import SYSTEM, build_messages


def test_messages_without_sql_have_system_and_user_only():
    msgs = build_messages("TABLE t (a INT)", "What's my GPA?")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == SYSTEM
    assert "TABLE t (a INT)" in msgs[1]["content"]
    assert "What's my GPA?" in msgs[1]["content"]


def test_messages_with_sql_add_trimmed_assistant_answer():
    msgs = build_messages("TABLE t (a INT)", "q", sql="  SELECT 1  \n")
    assert msgs[-1] == {"role": "assistant", "content": "SELECT 1"}
