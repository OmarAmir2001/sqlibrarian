"""Every fixed set of values the code uses, written once.

All are StrEnum: a member IS its string (f"{x}" gives "passed"), so it equals what
the database, JSON files and SQL return ("passed" == EnrollmentStatus.PASSED), and a
typo such as EnrollmentStatus.PASED fails loudly when the module is imported.
The values must match the CHECK constraints in db/records/01_schema.sql.
"""

from enum import StrEnum


class EnrollmentStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    WITHDRAWN = "withdrawn"
    INCOMPLETE = "incomplete"

    @classmethod
    def graded(cls) -> tuple["EnrollmentStatus", ...]:
        """The statuses that have marks and count in the GPA."""
        return (cls.PASSED, cls.FAILED)


class PlanStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PlanRule(StrEnum):
    """The rules the plan checker applies (also app_db.plan_flags.rule)."""

    MISSING_PREREQUISITE = "missing_prerequisite"
    OVER_CREDIT_LIMIT = "over_credit_limit"
    ALREADY_PASSED = "already_passed"


class Program(StrEnum):
    CS = "CS"
    IS = "IS"
    BOTH = "both"  # courses.program only: a course in both study plans


class Season(StrEnum):
    FALL = "fall"
    SPRING = "spring"
    SUMMER = "summer"


class TermType(StrEnum):
    """credit_rules.term_type: fall and spring are regular terms."""

    REGULAR = "regular"
    SUMMER = "summer"


class Lang(StrEnum):
    """Language of a question."""

    EN = "en"
    AR = "ar"  # Modern Standard Arabic
    MIXED = "mixed"  # Egyptian Arabic and/or Arabic mixed with English
    EG = "eg"  # Egyptian Arabic (rewrites); counted with mixed in the eval sets


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ScoreStatus(StrEnum):
    """Outcome of scoring one predicted query (evaluation/score.py)."""

    CORRECT = "correct"
    WRONG = "wrong"
    ERROR = "error"
    INVALID = "invalid"


class QuestionKind(StrEnum):
    """What a test question asks about, so the test set covers every use case."""

    GRADE = "grade"  # grade or mark in one course
    GPA = "gpa"  # cumulative or term GPA
    FAILED = "failed"  # failed or repeated courses
    CREDIT_HOURS = "credit_hours"  # hours passed, registered, electives
    COURSES_LEFT = "courses_left"  # required / elective courses still to take
    AVAILABLE_NEXT = "available_next"  # courses the student can take next term
    PREREQUISITES = "prerequisites"  # missing prerequisites for a course
    PLAN = "plan"  # next-term plan: status, courses
    THIS_TERM = "this_term"  # current registration, marks not recorded yet
    COMPARE_TERMS = "compare_terms"  # best / worst term, improvement
    PROFILE = "profile"  # advisor, program, level, credit limit
    OTHER = "other"


class Role(StrEnum):
    """app_db.users.role"""

    STUDENT = "student"
    TA = "ta"


class AskOutcome(StrEnum):
    """app_db.query_log.outcome"""

    ANSWERED = "answered"
    NO_ROWS = "no_rows"
    REJECTED = "rejected"  # the guard refused the generated SQL
    DB_ERROR = "db_error"  # still failing after the one retry
    MODEL_ERROR = "model_error"  # the model server failed or wrote no SQL
