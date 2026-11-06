"""The whole pipeline through the real API and both real databases; only the model is
replaced by a fake that answers with chosen SQL. Skipped when the databases aren't up.

What it proves: login works; answers are right for the logged-in student; row-level
security keeps every other student's rows out, even when the "model" asks for them;
the guard refuses writes; a failing query gets exactly one retry; query_log never
stores rows.
"""

import json
from pathlib import Path

import psycopg
import pytest

from sqlibrarian.helpers.config import get_settings

ROOT = Path(__file__).resolve().parents[1]
STUDENT, OTHER_STUDENT, TA = "238834", 233610, "ta10"


def _dbs_up() -> bool:
    s = get_settings()
    try:
        for url in (s.reader_db_url, s.app_db_url):
            psycopg.connect(url, connect_timeout=2).close()
        return bool(s.jwt_secret and s.demo_password)
    except (psycopg.Error, TypeError):
        return False


pytestmark = pytest.mark.skipif(not _dbs_up(), reason="databases / API settings not available")


class FakeGenerator:
    """Stands in for the model server: returns the next queued answer."""

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.calls: list[list[dict]] = []

    async def complete(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        return self.replies.pop(0)

    async def aclose(self) -> None:
        pass


@pytest.fixture(scope="module")
def api():
    from fastapi.testclient import TestClient

    from sqlibrarian.api.main import app

    fake = FakeGenerator()
    app.state.generator = fake
    with TestClient(app) as client:
        yield client, fake


def login(client, username: str) -> dict:
    password = get_settings().demo_password
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def ask(client, fake, headers, question: str, *replies: str) -> dict:
    fake.replies[:] = list(replies)
    r = client.post("/ask", json={"question": question}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_health(api):
    client, _ = api
    assert client.get("/health").json() == {"status": "ok", "records_db": "ok", "app_db": "ok"}


def test_login_refuses_a_wrong_password(api):
    client, _ = api
    r = client.post("/auth/login", json={"username": STUDENT, "password": "wrong"})
    assert r.status_code == 401


def test_ask_needs_a_token(api):
    client, _ = api
    assert client.post("/ask", json={"question": "my gpa"}).status_code == 401


def test_answer_for_the_logged_in_student(api):
    client, fake = api
    out = ask(
        client,
        fake,
        login(client, STUDENT),
        "What's my GPA?",
        "SELECT cumulative_gpa FROM students",
    )
    assert out["outcome"] == "answered"
    assert out["rows"] == [[0.99]] and out["answer"] == "The answer is 0.99."
    assert out["sql"].endswith("LIMIT 200")
    # the model saw the schema and the question, never an id
    assert STUDENT not in json.dumps(fake.calls[-1])


def test_arabic_question_gets_an_arabic_answer(api):
    client, fake = api
    out = ask(
        client,
        fake,
        login(client, STUDENT),
        "ما هو معدلي التراكمي؟",
        "SELECT cumulative_gpa FROM students",
    )
    assert out["answer"] == "الإجابة: 0.99"


def test_row_security_hides_other_students_even_if_asked_for(api):
    client, fake = api
    headers = login(client, STUDENT)
    everyone = ask(client, fake, headers, "show all students", "SELECT student_id FROM students")
    assert everyone["rows"] == [[int(STUDENT)]]
    other = ask(
        client, fake, headers, "grades of another student",
        f"SELECT total FROM enrollments WHERE student_id = {OTHER_STUDENT}",
    )  # fmt: skip
    assert other["outcome"] == "no_rows" and other["rows"] == []


def test_a_ta_sees_only_their_advisees(api):
    client, fake = api
    out = ask(client, fake, login(client, TA), "how many advisees", "SELECT count(*) FROM students")
    assert out["rows"] == [[50]]


def test_writes_are_refused_by_the_guard(api):
    client, fake = api
    out = ask(
        client,
        fake,
        login(client, STUDENT),
        "approve my plan",
        "UPDATE plans SET status = 'approved'",
    )
    assert out["outcome"] == "rejected" and out["rows"] == []


def test_one_retry_with_the_database_error(api):
    client, fake = api
    out = ask(
        client, fake, login(client, STUDENT), "my gpa",
        "SELECT gpa FROM students", "SELECT cumulative_gpa FROM students",
    )  # fmt: skip
    assert out["outcome"] == "answered" and "run_retry" in out["timings_ms"]
    assert "does not exist" in fake.calls[-1][-1]["content"]  # the error went back once


def test_query_log_has_no_rows(api):
    s = get_settings()
    with psycopg.connect(s.app_db_url) as conn:
        columns = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'query_log'"
        ).fetchall()]  # fmt: skip
        logged = conn.execute("SELECT count(*) FROM query_log").fetchone()[0]
    assert logged > 0
    assert not {"rows", "result", "data"} & set(columns)
