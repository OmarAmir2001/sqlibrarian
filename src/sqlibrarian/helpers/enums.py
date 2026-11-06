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
