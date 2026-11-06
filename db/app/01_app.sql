-- app_db: data owned by SQLibrarian itself (SDD section 7.2).
-- It never stores student records or returned rows; those live only in records_db.

CREATE TABLE users (
    user_id        integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username       text        NOT NULL UNIQUE,
    password_hash  text        NOT NULL,
    role           text        NOT NULL CHECK (role IN ('student', 'ta')),
    student_id     integer,
    ta_id          integer,
    created_at     timestamptz NOT NULL DEFAULT now(),
    -- A student account links to a student, a TA account to a TA, never both.
    CHECK (
        (role = 'student' AND student_id IS NOT NULL AND ta_id IS NULL)
        OR (role = 'ta' AND ta_id IS NOT NULL AND student_id IS NULL)
    )
);
COMMENT ON TABLE users IS 'Login accounts. student_id / ta_id point to records_db (another database, so no foreign key).';
COMMENT ON COLUMN users.password_hash IS 'bcrypt hash; the plain password is never stored.';

CREATE TABLE query_log (
    query_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id        integer     NOT NULL REFERENCES users (user_id),
    asked_at       timestamptz NOT NULL DEFAULT now(),
    question       text        NOT NULL,
    language       text        CHECK (language IN ('en', 'ar', 'mixed')),
    generated_sql  text,
    guard_verdict  text        CHECK (guard_verdict IN ('allowed', 'rejected')),
    guard_reason   text,
    row_count      integer,
    retried        boolean     NOT NULL DEFAULT false,
    timings_ms     jsonb,
    model_version  text,
    outcome        text        NOT NULL
                   CHECK (outcome IN ('answered', 'no_rows', 'rejected', 'db_error', 'model_error'))
);
CREATE INDEX query_log_asked_at ON query_log (asked_at);
COMMENT ON TABLE query_log IS 'One row per question. Never stores the rows a query returned. Kept 90 days.';
COMMENT ON COLUMN query_log.timings_ms IS 'Time per step in milliseconds, e.g. {"generate": 820, "guard": 3, "run": 41}.';

CREATE TABLE plan_flags (
    flag_id     integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plan_id     integer     NOT NULL,
    rule        text        NOT NULL
                CHECK (rule IN ('missing_prerequisite', 'over_credit_limit', 'already_passed')),
    details     text        NOT NULL,
    checked_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX plan_flags_plan_id ON plan_flags (plan_id);
COMMENT ON TABLE plan_flags IS 'Problems the nightly plan checker found. plan_id points to records_db.plans.';

CREATE TABLE audit_log (
    audit_id     bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      integer     REFERENCES users (user_id),
    action       text        NOT NULL CHECK (action IN ('approve', 'reject', 'query_refused')),
    target       text,
    details      jsonb,
    happened_at  timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE audit_log IS 'Every plan approval or rejection and every refused query: who, what, when.';
