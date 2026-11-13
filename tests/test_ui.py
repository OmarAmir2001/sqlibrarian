"""UI handlers with the API faked: every state shows the right thing."""

import httpx
import pytest

gr = pytest.importorskip("gradio")
from sqlibrarian.ui import app  # noqa: E402


def fake_api(handler):
    app._client = httpx.Client(base_url="http://api", transport=httpx.MockTransport(handler))


def test_login_returns_the_token_for_this_session_only():
    fake_api(lambda r: httpx.Response(200, json={"access_token": "t0k", "role": "student"}))
    token, status = app.login("238834", "pw")
    assert token == "t0k" and "student" in status


def test_wrong_password_is_a_clear_error():
    fake_api(lambda r: httpx.Response(401, json={"detail": "wrong username or password"}))
    with pytest.raises(gr.Error):
        app.login("238834", "bad")


def test_answer_sql_and_rows():
    body = {"answer": "The answer is 0.99.", "sql": "SELECT 1 LIMIT 200",
            "columns": ["cumulative_gpa"], "rows": [[0.99]], "outcome": "answered"}  # fmt: skip
    fake_api(lambda r: httpx.Response(200, json=body))
    answer, sql, table = app.ask_and_show("my gpa", "t0k")
    assert answer == "The answer is 0.99." and sql.startswith("SELECT")
    assert table.visible


def test_no_rows_hides_the_table_but_keeps_the_answer():
    body = {"answer": "I found no matching records.", "sql": "SELECT 1", "columns": [], "rows": [],
            "outcome": "no_rows"}  # fmt: skip
    fake_api(lambda r: httpx.Response(200, json=body))
    answer, _, table = app.ask_and_show("grade in Deep Learning", "t0k")
    assert "no matching" in answer and not table.visible


@pytest.mark.parametrize(
    ("status", "words"), [(401, "expired"), (422, "can't be sent"), (500, "HTTP 500")]
)
def test_api_errors_become_human_messages(status, words):
    fake_api(lambda r: httpx.Response(status, json={"detail": [{"msg": "too long"}]}))
    with pytest.raises(gr.Error, match=words):
        app.ask("q", "t0k")


def test_api_down():
    def boom(request):
        raise httpx.ConnectError("refused")

    fake_api(boom)
    with pytest.raises(gr.Error, match="not reachable"):
        app.ask("q", "t0k")


def test_must_log_in_and_type_something():
    with pytest.raises(gr.Error, match="Log in"):
        app.ask("q", None)
    with pytest.raises(gr.Error, match="Type a question"):
        app.ask("   ", "t0k")
