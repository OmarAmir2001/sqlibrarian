"""Create login accounts in app_db for every fake student and TA.

    uv run python db/seed_users.py

Usernames: the student id (e.g. 238834) and ta1 ... ta10. Every account gets the same
DEMO_PASSWORD from .env: the people are fake, and this is a demo. Safe to run again
(existing accounts are updated, not duplicated).
"""

import psycopg

from sqlibrarian.api.identity import hash_password
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Role
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

UPSERT = """
INSERT INTO users (username, password_hash, role, student_id, ta_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (username) DO UPDATE
SET password_hash = EXCLUDED.password_hash, role = EXCLUDED.role,
    student_id = EXCLUDED.student_id, ta_id = EXCLUDED.ta_id
"""


def main() -> None:
    configure_logging()
    s = get_settings()
    password_hash = hash_password(s.require("demo_password"))  # one hash: same password
    with psycopg.connect(s.records_dsn) as records:
        students = [r[0] for r in records.execute("SELECT student_id FROM students").fetchall()]
        tas = [r[0] for r in records.execute("SELECT ta_id FROM tas").fetchall()]
    rows = [(str(sid), password_hash, Role.STUDENT, sid, None) for sid in students]
    rows += [(f"ta{tid}", password_hash, Role.TA, None, tid) for tid in tas]
    with psycopg.connect(s.require("app_db_url")) as app, app.cursor() as cur:
        cur.executemany(UPSERT, rows)
    log.info("users.seeded", students=len(students), tas=len(tas))


if __name__ == "__main__":
    main()
