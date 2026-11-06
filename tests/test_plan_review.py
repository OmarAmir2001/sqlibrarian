"""UC3 + UC4 on the real databases: the checker finds exactly the plans broken on purpose,
and a TA can review only their own advisees' plans. Skipped when the databases are down."""

import json
from pathlib import Path

import psycopg
import pytest

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.plan_checker.rules import Catalog, StudentRecord, check_plan

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Catalog(
    hours={"A": 3, "B": 3, "GP": 3, "H": 2},
    prerequisites={"B": {"A"}},
    min_passed_hours={"GP": 95},
    credit_rules=[(3.0, 4.01, 21), (2.0, 3.0, 18), (1.0, 2.0, 15), (0.0, 1.0, 12)],
)


def rules(courses, **student):
    return [f.rule for f in check_plan(courses, StudentRecord(**student), CATALOG)]


def test_rules_without_a_database():
    assert rules(["B"], cumulative_gpa=3.0, passed=set()) == ["missing_prerequisite"]
    assert rules(["B"], cumulative_gpa=3.0, passed=set(), in_progress={"A"}) == []
    assert rules(["A"], cumulative_gpa=3.0, passed={"A"}) == ["already_passed"]
    assert rules(["GP"], cumulative_gpa=3.0, passed={"A"}) == ["missing_prerequisite"]
    assert rules(["A", "B", "H"], cumulative_gpa=0.5, passed={"A"}, in_progress=set()) == [
        "already_passed"
    ]
    many = ["A"] * 5  # 15 hours
    assert rules(many, cumulative_gpa=0.5, passed=set()) == ["over_credit_limit"]
    assert rules(many, cumulative_gpa=None, passed=set()) == []  # no GPA yet: 21 hours


def _dbs_up() -> bool:
    s = get_settings()
    try:
        for url in (s.checker_db_url, s.app_db_url, s.approver_db_url):
            psycopg.connect(url, connect_timeout=2).close()
        return bool(s.jwt_secret and s.demo_password)
    except (psycopg.Error, TypeError):
        return False


db = pytest.mark.skipif(not _dbs_up(), reason="databases not available")


@db
def test_checker_finds_exactly_the_plans_broken_on_purpose():
    from sqlibrarian.plan_checker.run import run_once

    results = run_once()
    expected = {}
    for line in (ROOT / "data/eval/plan_flags_expected.jsonl").read_text("utf-8").splitlines():
        e = json.loads(line)
        expected[e["plan_id"]] = e["rule"]
    flagged = {pid: {f.rule for f in flags} for pid, flags in results.items() if flags}
    assert set(flagged) == set(expected)  # no plan missed, no false alarm
    assert all(expected[pid] in found for pid, found in flagged.items())


@db
def test_a_ta_reviews_only_their_own_advisees():
    from fastapi.testclient import TestClient

    from sqlibrarian.api.main import app

    s = get_settings()
    with psycopg.connect(s.records_db_url) as conn:  # owner: pick two pending plans
        mine, theirs = conn.execute(
            "SELECT (SELECT p.plan_id FROM plans p JOIN students st USING (student_id)"
            "        WHERE p.status = 'pending' AND st.advisor_ta_id = 1 ORDER BY 1 LIMIT 1),"
            "       (SELECT p.plan_id FROM plans p JOIN students st USING (student_id)"
            "        WHERE p.status = 'pending' AND st.advisor_ta_id = 2 ORDER BY 1 LIMIT 1)"
        ).fetchone()
    try:
        with TestClient(app) as client:

            def token(user):
                r = client.post("/auth/login", json={"username": user, "password": s.demo_password})
                return {"Authorization": f"Bearer {r.json()['access_token']}"}

            ta1 = token("ta1")
            pending = client.get("/plans/pending", headers=ta1).json()
            assert pending and mine in {p["plan_id"] for p in pending}
            assert theirs not in {p["plan_id"] for p in pending}

            ok = client.post(f"/plans/{mine}/review", json={"decision": "approve"}, headers=ta1)
            assert ok.status_code == 200 and ok.json()["status"] == "approved"
            again = client.post(f"/plans/{mine}/review", json={"decision": "reject"}, headers=ta1)
            assert again.status_code == 409
            other = client.post(
                f"/plans/{theirs}/review", json={"decision": "approve"}, headers=ta1
            )
            assert other.status_code == 404  # not their advisee: invisible
            student = token("238834")
            denied = client.post(
                f"/plans/{mine}/review", json={"decision": "approve"}, headers=student
            )
            assert denied.status_code == 403
    finally:  # put the plan back, so the seed data stays as it was
        with psycopg.connect(s.records_db_url) as conn:
            conn.execute(
                "UPDATE plans SET status = 'pending', reviewed_by = NULL, reviewed_at = NULL"
                " WHERE plan_id = %s", (mine,),
            )  # fmt: skip
