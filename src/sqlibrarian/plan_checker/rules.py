"""The plan rules (UC3), as plain Python: no model involved (SDD decision).

A plan is the courses a student wants next term. Three rules, with the same meaning the
seed and the questions use:
  missing_prerequisite  a prerequisite is neither passed nor in progress this term, or the
                        course needs more passed hours (e.g. 95 for graduation project 1)
                        than the student will have
  already_passed        the student has already passed the course
  over_credit_limit     total credit hours above the limit for the student's cumulative
                        GPA (credit_rules, regular terms); 21 when there is no GPA yet
"""

from dataclasses import dataclass, field

from sqlibrarian.helpers.enums import PlanRule

NO_GPA_LIMIT = 21  # bylaws art. 14: a new student, who has no GPA yet


@dataclass(frozen=True)
class Catalog:
    hours: dict[str, int]  # course_code -> credit hours
    prerequisites: dict[str, set[str]]  # course_code -> courses that must be passed first
    min_passed_hours: dict[str, int]  # course_code -> passed hours needed (0 for most)
    credit_rules: list[tuple[float, float, int]]  # (gpa_from, gpa_to, max hours), regular terms

    def limit_for(self, gpa: float | None) -> int:
        if gpa is None:
            return NO_GPA_LIMIT
        return next(h for lo, hi, h in self.credit_rules if lo <= gpa < hi)


@dataclass(frozen=True)
class StudentRecord:
    cumulative_gpa: float | None
    passed: set[str]
    in_progress: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Flag:
    rule: PlanRule
    details: str


def check_plan(courses: list[str], student: StudentRecord, catalog: Catalog) -> list[Flag]:
    flags = []
    done = student.passed | student.in_progress  # counts as passed by next term
    done_hours = sum(catalog.hours[c] for c in done)
    for code in sorted(courses):
        missing = sorted(catalog.prerequisites.get(code, set()) - done)
        if missing:
            flags.append(Flag(PlanRule.MISSING_PREREQUISITE, f"{code} needs {', '.join(missing)}"))
        needed = catalog.min_passed_hours.get(code, 0)
        if needed > done_hours:
            flags.append(
                Flag(PlanRule.MISSING_PREREQUISITE, f"{code} needs {needed} passed hours, "
                     f"the student will have {done_hours}")
            )  # fmt: skip
        if code in student.passed:
            flags.append(Flag(PlanRule.ALREADY_PASSED, f"{code} was already passed"))
    hours = sum(catalog.hours[c] for c in courses)
    limit = catalog.limit_for(student.cumulative_gpa)
    if hours > limit:
        gpa = student.cumulative_gpa if student.cumulative_gpa is not None else "none yet"
        flags.append(
            Flag(PlanRule.OVER_CREDIT_LIMIT, f"{hours} credit hours, limit {limit} for GPA {gpa}")
        )
    return flags
