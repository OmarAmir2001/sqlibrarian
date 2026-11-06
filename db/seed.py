"""Fill records_db with fake TAs, students, enrollments and plans.

Everything here is invented. The only real inputs are the course catalog
(02_catalog.sql, from the bylaws) and a few counts from the institute (share of
students per level); no real name or student code is used.

The same seed always produces the same data, so results can be compared across runs.
Running it again wipes the people tables and refills them; the catalog stays.

Run (records_db up, RECORDS_DB_URL in .env):
    uv run python db/seed.py

It also writes data/eval/plan_flags_expected.jsonl: the plans broken on purpose,
which the P6 plan checker must find exactly.
"""

import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg

from sqlibrarian.config import get_settings

SEED = 42
N_STUDENTS = 500
N_TAS = 10
LEVEL_SHARE = {1: 0.15, 2: 0.37, 3: 0.27, 4: 0.21}  # from the institute's 2025-26 counts
IS_SHARE = 0.20  # the real share is ~3.5%; raised so IS courses have data
LAGGING_SHARE = 0.20  # students who started a year earlier than their level suggests
PLAN_SHARE = 0.60  # students with a plan for next term
BROKEN_SHARE = 0.15  # plans that break a rule on purpose

CURRENT_TERM = 7  # fall 2026
PLAN_TERM = 8  # spring 2027
ELECTIVE_HOURS = 21
PLAN_TIME = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)

# First guess at course difficulty (Omar to adjust). Everything else is medium.
HARD = {
    "BS 101", "BS 102", "BS 103", "BS 201", "BS 203",
    "CS 201", "CS 203", "CS 301", "CS 306", "CS 309", "CS 403", "CS 419",
    "IS 301", "IS 303",
}  # fmt: skip
EASY_PREFIXES = ("H ",)
EASY = {"CS 101", "IS 105", "IS 207", "CS 104", "CS 413", "CS 498", "CS 499", "IS 498", "IS 499"}
MEAN_TOTAL = {"easy": 75, "medium": 68, "hard": 60}
SPREAD = 12

# Bylaws article 20: a course retaken after failing is capped at B+ (1st retake),
# C+ (2nd) and D+ (later). Value = highest total mark kept.
RETAKE_CAP = {1: 87.0, 2: 75.0}
RETAKE_CAP_LATER = 63.0

# --- Names ---------------------------------------------------------------------------
# (Arabic, English spellings). Several spellings of one name are common on official
# documents, so a student's English name uses one of them at random.

MUSLIM_MALE = [
    ("محمد", ["Mohamed", "Muhammad", "Mohammed"]), ("أحمد", ["Ahmed", "Ahmad"]),
    ("محمود", ["Mahmoud", "Mahmud"]), ("مصطفى", ["Mostafa", "Mustafa", "Moustafa"]),
    ("عمر", ["Omar", "Omer"]), ("يوسف", ["Youssef", "Yousef", "Yusuf"]),
    ("علي", ["Ali", "Aly"]), ("حسن", ["Hassan", "Hasan"]), ("حسين", ["Hussein", "Hossein"]),
    ("إبراهيم", ["Ibrahim", "Ebrahim"]), ("خالد", ["Khaled", "Khalid"]),
    ("عبدالرحمن", ["Abdelrahman", "Abdulrahman", "Abd El-Rahman"]),
    ("عبدالله", ["Abdallah", "Abdullah"]), ("كريم", ["Karim", "Kareem"]),
    ("طارق", ["Tarek", "Tareq"]), ("هشام", ["Hesham", "Hisham"]), ("وليد", ["Walid", "Waleed"]),
    ("ياسر", ["Yasser", "Yasir"]), ("عمرو", ["Amr"]), ("زياد", ["Ziad", "Zeyad"]),
    ("مازن", ["Mazen"]), ("سيف", ["Seif", "Saif"]), ("أنس", ["Anas"]), ("بلال", ["Belal", "Bilal"]),
    ("حمزة", ["Hamza"]), ("شريف", ["Sherif", "Shereef"]), ("عادل", ["Adel"]),
    ("سامح", ["Sameh"]), ("أشرف", ["Ashraf"]), ("جمال", ["Gamal", "Jamal"]),
    ("سعيد", ["Said", "Saeed"]), ("صلاح", ["Salah"]), ("رضا", ["Reda", "Rida"]),
]  # fmt: skip
CHRISTIAN_MALE = [
    ("مينا", ["Mina", "Meena"]), ("أبانوب", ["Abanoub", "Abanob"]),
    ("كيرلس", ["Kirollos", "Kerolos", "Kyrillos"]), ("بيشوي", ["Bishoy", "Beshoy"]),
    ("جرجس", ["Girgis", "Gerges"]), ("مرقس", ["Morcos", "Marcos"]), ("بيتر", ["Peter"]),
    ("مايكل", ["Michael"]), ("بولس", ["Boules", "Paulis"]), ("فادي", ["Fady", "Fadi"]),
    ("رامي", ["Ramy", "Rami"]), ("مجدي", ["Magdy", "Magdi"]), ("عماد", ["Emad", "Imad"]),
    ("سامي", ["Samy", "Sami"]), ("نادر", ["Nader"]), ("هاني", ["Hany", "Hani"]),
]  # fmt: skip
MUSLIM_FEMALE = [
    ("فاطمة", ["Fatma", "Fatima"]), ("مريم", ["Mariam", "Maryam"]), ("نور", ["Nour", "Noor"]),
    ("سارة", ["Sara", "Sarah"]), ("هبة", ["Heba"]), ("آية", ["Aya", "Ayah"]),
    ("منة", ["Menna", "Mennah"]), ("ياسمين", ["Yasmine", "Yasmin"]), ("رنا", ["Rana"]),
    ("سلمى", ["Salma"]), ("ندى", ["Nada"]), ("دينا", ["Dina"]), ("شيماء", ["Shaimaa", "Shimaa"]),
    ("إسراء", ["Esraa", "Israa"]), ("رحمة", ["Rahma"]), ("حبيبة", ["Habiba"]),
    ("جنى", ["Jana", "Gana"]), ("خديجة", ["Khadija", "Khadiga"]), ("ملك", ["Malak"]),
]  # fmt: skip
CHRISTIAN_FEMALE = [
    ("مارينا", ["Marina"]), ("ماريان", ["Marian", "Mariane"]), ("مريم", ["Mariam", "Maryam"]),
    ("كاترين", ["Catherine", "Katrin"]), ("ميرنا", ["Mirna", "Merna"]), ("دميانة", ["Demiana"]),
    ("مونيكا", ["Monica"]), ("كريستين", ["Christine"]), ("ساندرا", ["Sandra"]), ("نور", ["Nour"]),
]  # fmt: skip
MUSLIM_FAMILY = [
    ("عبدالعزيز", ["Abdelaziz"]), ("السيد", ["El-Sayed", "Elsayed"]), ("منصور", ["Mansour"]),
    ("عثمان", ["Osman", "Othman"]), ("سليمان", ["Soliman", "Suleiman"]), ("فرج", ["Farag"]),
    ("عبدالحميد", ["Abdelhamid"]), ("رمضان", ["Ramadan"]), ("النجار", ["El-Naggar", "Elnaggar"]),
    ("الشريف", ["El-Sherif"]), ("حسانين", ["Hassanein"]), ("زكي", ["Zaki", "Zaky"]),
    ("شحاتة", ["Shehata"]), ("عوض", ["Awad"]), ("الشافعي", ["El-Shafei"]), ("بدوي", ["Badawy"]),
]  # fmt: skip
CHRISTIAN_FAMILY = [
    ("نصيف", ["Nassif"]), ("بطرس", ["Botros", "Boutros"]), ("حنا", ["Hanna"]),
    ("ميخائيل", ["Mikhail"]), ("غالي", ["Ghaly", "Ghali"]), ("تادرس", ["Tadros"]),
    ("عزيز", ["Aziz"]), ("لبيب", ["Labib"]), ("شنودة", ["Shenouda"]), ("فهمي", ["Fahmy"]),
]  # fmt: skip
CHRISTIAN_SHARE = 0.15


def fake_name(rng: random.Random, female: bool) -> tuple[str, str]:
    """Four-part Egyptian name (first, father, grandfather, family) in Arabic and English."""
    christian = rng.random() < CHRISTIAN_SHARE
    if christian:
        firsts, males, family = (
            CHRISTIAN_FEMALE if female else CHRISTIAN_MALE,
            CHRISTIAN_MALE,
            CHRISTIAN_FAMILY,
        )
    else:
        firsts, males, family = MUSLIM_FEMALE if female else MUSLIM_MALE, MUSLIM_MALE, MUSLIM_FAMILY
    parts = [rng.choice(firsts), rng.choice(males), rng.choice(males), rng.choice(family)]
    return " ".join(p[0] for p in parts), " ".join(rng.choice(p[1]) for p in parts)


# --- Catalog -------------------------------------------------------------------------


@dataclass
class Course:
    code: str
    hours: int
    level: int
    program: str
    required: bool
    min_passed_hours: int
    prereqs: set[str] = field(default_factory=set)

    @property
    def difficulty(self) -> str:
        if self.code in HARD:
            return "hard"
        if self.code in EASY or self.code.startswith(EASY_PREFIXES):
            return "easy"
        return "medium"


def load_catalog(cur: psycopg.Cursor) -> tuple[dict[str, Course], list, list]:
    cur.execute(
        "SELECT course_code, credit_hours, level, program, is_required, min_passed_hours"
        " FROM courses"
    )
    courses = {r[0]: Course(*r) for r in cur.fetchall()}
    cur.execute("SELECT course_code, required_course_code FROM prerequisites")
    for code, req in cur.fetchall():
        courses[code].prereqs.add(req)
    cur.execute("SELECT min_total, letter_grade, points FROM grading_scale ORDER BY min_total DESC")
    scale = [(float(m), letter, float(p)) for m, letter, p in cur.fetchall()]
    cur.execute(
        "SELECT gpa_from, gpa_to, max_credit_hours FROM credit_rules WHERE term_type = 'regular'"
    )
    rules = [(float(a), float(b), h) for a, b, h in cur.fetchall()]
    return courses, scale, rules


def letter_for(total: float, scale: list) -> tuple[str, float]:
    for min_total, letter, points in scale:
        if total >= min_total:
            return letter, points
    raise ValueError(total)


def credit_limit(gpa: float | None, rules: list) -> int:
    """Article 14. gpa None = first term of a new student."""
    if gpa is None:
        return 21
    return next(h for lo, hi, h in rules if lo <= gpa < hi)


# --- Students ------------------------------------------------------------------------


@dataclass
class Student:
    student_id: int
    name_ar: str
    name_en: str
    program: str
    level: int
    entry_term: int
    lagging: bool
    ability: float
    advisor: int
    attempts: dict[str, list[dict]] = field(default_factory=dict)  # course -> rows
    gpa: float | None = None

    def passed(self) -> set[str]:
        return {
            c for c, rows in self.attempts.items() if any(r["status"] == "passed" for r in rows)
        }

    def passed_hours(self, courses: dict[str, Course]) -> int:
        return sum(courses[c].hours for c in self.passed())

    def recompute_gpa(self, courses: dict[str, Course]) -> None:
        """Latest graded attempt of each course counts (article 20)."""
        points = hours = 0.0
        for code, rows in self.attempts.items():
            graded = [r for r in rows if r["status"] in ("passed", "failed")]
            if graded:
                points += graded[-1]["grade_points"] * courses[code].hours
                hours += courses[code].hours
        self.gpa = round(points / hours, 2) if hours else None


def choose_courses(
    s: Student,
    courses: dict[str, Course],
    rules: list,
    term: int,
    rng: random.Random,
    done: set[str],
    in_progress: frozenset[str] = frozenset(),
) -> list[str]:
    """Pick a believable load: retakes first, then the study plan in level order,
    electives from year 3, within the credit limit for the current GPA.
    `done` = courses that count as passed for prerequisites."""
    year = (term - s.entry_term) // 2 + 1
    limit = credit_limit(s.gpa if s.attempts else None, rules)
    target = rng.choice([12, 15]) if s.lagging else rng.choice([15, 18, 18, 18, 21])
    target = min(target, limit)
    hours_done = sum(courses[c].hours for c in done)
    elective_hours = sum(courses[c].hours for c in done if not courses[c].required)

    failed_before = {c for c, rows in s.attempts.items() if c not in done}
    candidates = []
    for c in courses.values():
        if c.program not in (s.program, "both") or c.code in done or c.code in in_progress:
            continue
        if not c.prereqs <= done or c.min_passed_hours > hours_done:
            continue
        if c.level > year + 1:
            continue
        if not c.required and (year < 3 or c.code.endswith(("310", "405"))):
            continue  # electives from year 3; "selected topics" have no fixed content
        candidates.append(c)
    rng.shuffle(candidates)
    candidates.sort(key=lambda c: (c.code not in failed_before, c.level, not c.required))

    picked, hours = [], 0
    for c in candidates:
        if hours + c.hours > target:
            continue
        if not c.required:
            if elective_hours >= ELECTIVE_HOURS:
                continue
            elective_hours += c.hours
        picked.append(c.code)
        hours += c.hours
    return picked


def grade(s: Student, course: Course, scale: list, rng: random.Random, attempt_no: int) -> dict:
    """One finished course: marks split by the bylaws' weights (20 + 20 + 60)."""
    r = rng.random()
    if r < 0.02:
        return {"status": "withdrawn"}
    if r < 0.025:
        return {"status": "incomplete"}
    total = rng.gauss(MEAN_TOTAL[course.difficulty] + s.ability, SPREAD)
    total = round(min(100.0, max(5.0, total)), 1)
    share = total / 100
    midterm = round(min(20.0, max(0.0, rng.gauss(20 * share, 2))), 1)
    coursework = round(min(20.0, max(0.0, rng.gauss(20 * share + 1, 2))), 1)
    final = round(min(60.0, max(0.0, total - midterm - coursework)), 1)
    total = round(midterm + coursework + final, 1)

    retakes = attempt_no - 1  # earlier attempts of this course
    failed_before = retakes > 0 and any(
        r["status"] == "failed" for r in s.attempts.get(course.code, [])
    )
    kept = total
    if failed_before:
        kept = min(total, RETAKE_CAP.get(retakes, RETAKE_CAP_LATER))
    letter, points = letter_for(kept, scale)
    return {
        "midterm": midterm, "coursework": coursework, "final": final, "total": kept,
        "letter_grade": letter, "grade_points": points,
        "status": "passed" if letter != "F" else "failed",
    }  # fmt: skip


def make_students(rng: random.Random) -> list[Student]:
    students, used_ids = [], set()
    levels = [lvl for lvl, share in LEVEL_SHARE.items() for _ in range(round(share * N_STUDENTS))]
    for i, level in enumerate(levels):
        # Normal start: fall of (2026 - level + 1). Term ids: fall 2023 = 1, fall 2024 = 3, ...
        entry_term = CURRENT_TERM - 2 * (level - 1)
        lagging = level < 4 and rng.random() < LAGGING_SHARE
        if lagging:
            entry_term -= 2
        entry_year = 2023 + (entry_term - 1) // 2
        while True:  # 6-digit code, first two digits = entry year, like the institute's
            sid = int(f"{entry_year % 100}{rng.randint(0, 9999):04d}")
            if sid not in used_ids:
                used_ids.add(sid)
                break
        name_ar, name_en = fake_name(rng, female=rng.random() < 0.4)
        students.append(
            Student(
                student_id=sid, name_ar=name_ar, name_en=name_en,
                program="IS" if rng.random() < IS_SHARE else "CS",
                level=level, entry_term=entry_term, lagging=lagging,
                ability=rng.gauss(-4 if lagging else 0, 7),
                advisor=1 + i % N_TAS,
            )
        )  # fmt: skip
    return students


def simulate(s: Student, courses, scale, rules, rng) -> tuple[list[tuple], list[str]]:
    """Walk from the student's first term to now. Returns finished enrollment rows
    and the courses in progress this term."""
    rows = []
    for term in range(s.entry_term, CURRENT_TERM):
        for code in choose_courses(s, courses, rules, term, rng, s.passed()):
            attempt_no = len(s.attempts.get(code, [])) + 1
            g = grade(s, courses[code], scale, rng, attempt_no)
            s.attempts.setdefault(code, []).append(g)
            rows.append((s.student_id, code, term, g))
        s.recompute_gpa(courses)
    current = choose_courses(s, courses, rules, CURRENT_TERM, rng, s.passed())
    return rows, current


def make_plan(s, current, courses, rules, rng) -> tuple[list[str], str | None, str]:
    """Next-term plan. In-progress courses count as passed for prerequisites.
    Returns (courses, broken rule or None, details)."""
    done = s.passed() | set(current)
    picked = choose_courses(s, courses, rules, PLAN_TERM, rng, done)
    if not picked or rng.random() >= BROKEN_SHARE:
        return picked, None, ""
    limit = credit_limit(s.gpa, rules)
    hours = sum(courses[c].hours for c in picked)
    in_program = [c for c in courses.values() if c.program in (s.program, "both")]
    kind = rng.choice(["missing_prerequisite", "over_credit_limit", "already_passed"])
    if kind == "missing_prerequisite":
        options = [
            c for c in in_program if c.code not in done | set(picked) and not c.prereqs <= done
        ]
        if options:
            c = rng.choice(options)
            missing = sorted(c.prereqs - done)
            return picked + [c.code], kind, f"{c.code} needs {', '.join(missing)}"
    if kind == "already_passed" and s.passed():
        c = rng.choice(sorted(s.passed()))
        return picked + [c], kind, f"{c} was already passed"
    # over the limit: add eligible-looking courses until hours exceed the GPA limit
    extra = [c for c in in_program if c.code not in done and c.code not in picked
             and c.prereqs <= done and c.min_passed_hours <= s.passed_hours(courses)]  # fmt: skip
    rng.shuffle(extra)
    for c in extra:
        if hours > limit:
            break
        picked.append(c.code)
        hours += c.hours
    if hours > limit:
        gpa = s.gpa if s.gpa is not None else "none yet (new student)"
        return picked, "over_credit_limit", f"{hours} credit hours, limit {limit} for GPA {gpa}"
    return picked, None, ""


# --- Database ------------------------------------------------------------------------


def main() -> None:
    rng = random.Random(SEED)
    expected = []
    with psycopg.connect(get_settings().records_db_url) as conn, conn.cursor() as cur:
        courses, scale, rules = load_catalog(cur)
        cur.execute("TRUNCATE plan_courses, plans, enrollments, students, tas RESTART IDENTITY")

        tas = []
        for ta_id in range(1, N_TAS + 1):
            name_ar, _ = fake_name(rng, female=rng.random() < 0.5)
            tas.append((ta_id, " ".join(name_ar.split()[:2] + name_ar.split()[-1:])))
        cur.executemany("INSERT INTO tas (ta_id, name) VALUES (%s, %s)", tas)

        students = make_students(rng)
        enrollment_rows, plans = [], []
        for s in students:
            rows, current = simulate(s, courses, scale, rules, rng)
            enrollment_rows += rows
            enrollment_rows += [(s.student_id, c, CURRENT_TERM, {"status": "in_progress"})
                                for c in current]  # fmt: skip
            if rng.random() < PLAN_SHARE:
                picked, rule, details = make_plan(s, current, courses, rules, rng)
                if picked:
                    plans.append((s, picked, rule, details))

        cur.executemany(
            "INSERT INTO students (student_id, name_ar, name_en, program, level,"
            " cumulative_gpa, advisor_ta_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(s.student_id, s.name_ar, s.name_en, s.program, s.level, s.gpa, s.advisor)
             for s in students],
        )  # fmt: skip
        cols = ["midterm", "coursework", "final", "total", "letter_grade", "grade_points"]
        cur.executemany(
            "INSERT INTO enrollments (student_id, course_code, term_id, midterm, coursework,"
            " final, total, letter_grade, grade_points, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(sid, code, term, *(g.get(c) for c in cols), g["status"])
             for sid, code, term, g in enrollment_rows],
        )  # fmt: skip

        for s, picked, rule, details in plans:
            created = PLAN_TIME + timedelta(hours=rng.randint(0, 96))
            status = "pending"
            if rule is None:
                status = rng.choices(["pending", "approved", "rejected"], [6, 3, 1])[0]
            reviewed = (
                (s.advisor, created + timedelta(days=1)) if status != "pending" else (None, None)
            )
            cur.execute(
                "INSERT INTO plans (student_id, term_id, created_at, status, reviewed_by,"
                " reviewed_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING plan_id",
                (s.student_id, PLAN_TERM, created, status, *reviewed),
            )
            plan_id = cur.fetchone()[0]
            cur.executemany(
                "INSERT INTO plan_courses (plan_id, course_code) VALUES (%s, %s)",
                [(plan_id, c) for c in picked],
            )
            if rule:
                expected.append({"plan_id": plan_id, "student_id": s.student_id,
                                 "rule": rule, "details": details})  # fmt: skip

    out = Path(__file__).resolve().parents[1] / "data/eval/plan_flags_expected.jsonl"
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in expected), "utf-8")
    print(f"{len(tas)} TAs, {len(students)} students, {len(enrollment_rows)} enrollments, "
          f"{len(plans)} plans ({len(expected)} broken on purpose -> {out.name})")  # fmt: skip


if __name__ == "__main__":
    main()
