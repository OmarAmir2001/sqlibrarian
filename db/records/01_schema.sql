-- records_db: the simulated Student Records System.
--
-- Every table and column has a COMMENT. db/export_schema.py turns these comments into
-- data/eval/schema.txt, the only description of the database the model ever reads,
-- so they are written for someone who has never seen the institute.
--
-- Tables are created so that each one exists before anything points to it.

-- Terms --------------------------------------------------------------------------------

CREATE TABLE terms (
    term_id     integer PRIMARY KEY,
    year        integer NOT NULL CHECK (year BETWEEN 2000 AND 2100),
    season      text    NOT NULL CHECK (season IN ('fall', 'spring', 'summer')),
    is_current  boolean NOT NULL DEFAULT false,
    registration_opens   date,
    registration_closes  date,
    UNIQUE (year, season),
    CHECK (registration_closes >= registration_opens)
);
-- At most one term can be the current one.
CREATE UNIQUE INDEX terms_one_current ON terms (is_current) WHERE is_current;

COMMENT ON TABLE terms IS
    'Academic terms. Each year has a fall and a spring term, sometimes a summer term.';
COMMENT ON COLUMN terms.term_id IS
    'Term number; a higher number is a later term.';
COMMENT ON COLUMN terms.year IS
    'Calendar year the term starts in (spring 2027 has year 2027).';
COMMENT ON COLUMN terms.season IS
    '''fall'', ''spring'' or ''summer''.';
COMMENT ON COLUMN terms.is_current IS
    'True only for the term running now ("this term").';
COMMENT ON COLUMN terms.registration_opens IS
    'First day of course registration for this term; empty if not announced.';
COMMENT ON COLUMN terms.registration_closes IS
    'Last day of registration; it is open when CURRENT_DATE is between the two dates.';

-- Courses ------------------------------------------------------------------------------

CREATE TABLE courses (
    course_code          text    PRIMARY KEY CHECK (course_code ~ '^(CS|IS|BS|H) [1-4][0-9]{2}$'),
    title_en             text    NOT NULL,
    title_ar             text    NOT NULL,
    credit_hours         integer NOT NULL CHECK (credit_hours BETWEEN 1 AND 6),
    level                integer NOT NULL CHECK (level BETWEEN 1 AND 4),
    semester             text    NOT NULL CHECK (semester IN ('fall', 'spring')),
    program              text    NOT NULL CHECK (program IN ('CS', 'IS', 'both')),
    is_required          boolean NOT NULL,
    min_passed_hours     integer NOT NULL DEFAULT 0 CHECK (min_passed_hours >= 0)
);

COMMENT ON TABLE courses IS
    'Every course in the Computer Science (CS) and Information Systems (IS) study plans.';
COMMENT ON COLUMN courses.course_code IS
    'e.g. ''CS 201''. CS = computer science, IS = information systems, BS = basic sciences, H = humanities.';
COMMENT ON COLUMN courses.title_en IS
    'English name, e.g. ''Data Structures''.';
COMMENT ON COLUMN courses.title_ar IS
    'Arabic name, e.g. ''هياكل البيانات''.';
COMMENT ON COLUMN courses.credit_hours IS
    '3 for most courses, 2 for humanities (H).';
COMMENT ON COLUMN courses.level IS
    'Study-plan year it is placed in, 1 to 4 (not always the first digit of the code).';
COMMENT ON COLUMN courses.semester IS
    'Study-plan term it is placed in: ''fall'' or ''spring''.';
COMMENT ON COLUMN courses.program IS
    'Study plan it belongs to: ''CS'', ''IS'' or ''both''.';
COMMENT ON COLUMN courses.is_required IS
    'True if required in its program; false for an elective.';
COMMENT ON COLUMN courses.min_passed_hours IS
    'Passed hours needed before taking it: 0, or 96 for graduation project 1.';

-- Prerequisites ------------------------------------------------------------------------

CREATE TABLE prerequisites (
    course_code           text NOT NULL REFERENCES courses (course_code),
    required_course_code  text NOT NULL REFERENCES courses (course_code),
    PRIMARY KEY (course_code, required_course_code),
    CHECK (course_code <> required_course_code)
);

COMMENT ON TABLE prerequisites IS
    'Courses that must be passed before another course can be taken.';
COMMENT ON COLUMN prerequisites.course_code IS
    'The course that has the prerequisite.';
COMMENT ON COLUMN prerequisites.required_course_code IS
    'The course that must be passed first.';

-- Course offerings -------------------------------------------------------------------

CREATE TABLE course_offerings (
    term_id         integer NOT NULL REFERENCES terms (term_id),
    course_code     text    NOT NULL REFERENCES courses (course_code),
    graduates_only  boolean NOT NULL DEFAULT false,
    PRIMARY KEY (term_id, course_code)
);

COMMENT ON TABLE course_offerings IS
    'Courses open for registration in a term; no row means not offered. The next term has no rows until announced.';
COMMENT ON COLUMN course_offerings.term_id IS
    '-';
COMMENT ON COLUMN course_offerings.course_code IS
    '-';
COMMENT ON COLUMN course_offerings.graduates_only IS
    'Only graduation candidates may register it.';

-- Registration rules ------------------------------------------------------------------

CREATE TABLE registration_policy (
    rule_key     text    PRIMARY KEY,
    value        numeric NOT NULL,
    description  text    NOT NULL
);

COMMENT ON TABLE registration_policy IS
    'Fixed academic rules, one row each.';
COMMENT ON COLUMN registration_policy.rule_key IS
    'Rule name: ''min_credit_hours'', ''warning_gpa'', ''at_risk_consecutive_warnings'', ''at_risk_cumulative_warnings'', ''dismissal_consecutive_warnings'', ''dismissal_cumulative_warnings'', ''graduation_hours'' or ''max_elective_courses''.';
COMMENT ON COLUMN registration_policy.value IS
    'The rule''s number, e.g. 9 for min_credit_hours.';
COMMENT ON COLUMN registration_policy.description IS
    '-';

-- Credit-hour limits -------------------------------------------------------------------

CREATE TABLE credit_rules (
    term_type         text         NOT NULL CHECK (term_type IN ('regular', 'summer')),
    gpa_from          numeric(3,2) NOT NULL CHECK (gpa_from >= 0),
    gpa_to            numeric(3,2) NOT NULL CHECK (gpa_to > gpa_from),
    max_credit_hours  integer      NOT NULL CHECK (max_credit_hours > 0),
    PRIMARY KEY (term_type, gpa_from)
);

COMMENT ON TABLE credit_rules IS
    'Most credit hours a student may register in a term, by cumulative GPA. A new student may register 21.';
COMMENT ON COLUMN credit_rules.term_type IS
    '''regular'' (fall, spring) or ''summer''.';
COMMENT ON COLUMN credit_rules.gpa_from IS
    'Band start (included).';
COMMENT ON COLUMN credit_rules.gpa_to IS
    'Band end (not included); the top band ends at 4.01.';
COMMENT ON COLUMN credit_rules.max_credit_hours IS
    'Hours allowed in this band.';

-- Grading scale ------------------------------------------------------------------------

CREATE TABLE grading_scale (
    letter_grade  text         PRIMARY KEY,
    min_total     numeric(5,2) NOT NULL CHECK (min_total BETWEEN 0 AND 100),
    points        numeric(2,1) NOT NULL CHECK (points BETWEEN 0 AND 4)
);

COMMENT ON TABLE grading_scale IS
    'Total mark out of 100 to letter grade and points. F (below 50) fails; other letters pass.';
COMMENT ON COLUMN grading_scale.letter_grade IS
    '''A+'' (best) to ''F''.';
COMMENT ON COLUMN grading_scale.min_total IS
    'Lowest total for this letter.';
COMMENT ON COLUMN grading_scale.points IS
    'Grade points, 4.0 (A+) to 0 (F).';

-- Teaching assistants (academic advisors) ----------------------------------------------

CREATE TABLE tas (
    ta_id  integer PRIMARY KEY,
    name   text    NOT NULL
);

COMMENT ON TABLE tas IS
    'Teaching assistants; they are the academic advisors.';
COMMENT ON COLUMN tas.ta_id IS
    '-';
COMMENT ON COLUMN tas.name IS
    'Full name.';

-- Students -----------------------------------------------------------------------------

CREATE TABLE students (
    student_id      integer      PRIMARY KEY,
    name_ar         text         NOT NULL,
    name_en         text         NOT NULL,
    program         text         NOT NULL CHECK (program IN ('CS', 'IS')),
    level           integer      NOT NULL CHECK (level BETWEEN 1 AND 4),
    cumulative_gpa  numeric(3,2) CHECK (cumulative_gpa BETWEEN 0 AND 4),
    completed_credit_hours  integer NOT NULL DEFAULT 0 CHECK (completed_credit_hours >= 0),
    academic_standing       text    NOT NULL DEFAULT 'good_standing'
                            CHECK (academic_standing IN ('good_standing', 'at_risk', 'on_probation',
                                                         'improvement_plan_previous', 'dismissed')),
    consecutive_warnings    integer NOT NULL DEFAULT 0 CHECK (consecutive_warnings >= 0),
    cumulative_warnings     integer NOT NULL DEFAULT 0 CHECK (cumulative_warnings >= consecutive_warnings),
    graduation_candidate    boolean NOT NULL DEFAULT false,
    advisor_ta_id   integer      NOT NULL REFERENCES tas (ta_id)
);
CREATE INDEX students_advisor_ta_id ON students (advisor_ta_id);

COMMENT ON TABLE students IS
    'Students (only the one asking, or a TA''s advisees).';
COMMENT ON COLUMN students.student_id IS
    '-';
COMMENT ON COLUMN students.name_ar IS
    'Full name in Arabic.';
COMMENT ON COLUMN students.name_en IS
    'Full name in English letters.';
COMMENT ON COLUMN students.program IS
    '''CS'' or ''IS''.';
COMMENT ON COLUMN students.level IS
    'Year of study, 1 to 4.';
COMMENT ON COLUMN students.cumulative_gpa IS
    'Cumulative GPA, 0.00 to 4.00; empty for a new student.';
COMMENT ON COLUMN students.completed_credit_hours IS
    'Credit hours passed so far.';
COMMENT ON COLUMN students.academic_standing IS
    '''good_standing'', ''at_risk'' (near dismissal), ''on_probation'' (on an improvement plan), ''improvement_plan_previous'' or ''dismissed''.';
COMMENT ON COLUMN students.consecutive_warnings IS
    'Academic warnings in a row (a fall/spring term ending with GPA below warning_gpa gives one; a term above resets it).';
COMMENT ON COLUMN students.cumulative_warnings IS
    'All academic warnings so far.';
COMMENT ON COLUMN students.graduation_candidate IS
    'Can reach the 135 graduation hours by the end of next term.';
COMMENT ON COLUMN students.advisor_ta_id IS
    'The advisor.';

-- Enrollments (one row per course a student took in a term) ----------------------------

CREATE TABLE enrollments (
    enrollment_id  integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id     integer      NOT NULL REFERENCES students (student_id),
    course_code    text         NOT NULL REFERENCES courses (course_code),
    term_id        integer      NOT NULL REFERENCES terms (term_id),
    midterm        numeric(4,1) CHECK (midterm BETWEEN 0 AND 20),
    coursework     numeric(4,1) CHECK (coursework BETWEEN 0 AND 20),
    final          numeric(4,1) CHECK (final BETWEEN 0 AND 60),
    total          numeric(4,1) CHECK (total BETWEEN 0 AND 100),
    letter_grade   text         REFERENCES grading_scale (letter_grade),
    grade_points   numeric(2,1) CHECK (grade_points BETWEEN 0 AND 4),
    status         text         NOT NULL
                   CHECK (status IN ('passed', 'failed', 'in_progress', 'withdrawn', 'incomplete')),
    UNIQUE (student_id, course_code, term_id),
    -- A graded course has all its marks; an ungraded one has none.
    CHECK (
        (status IN ('passed', 'failed')
            AND total IS NOT NULL AND letter_grade IS NOT NULL AND grade_points IS NOT NULL)
        OR (status IN ('in_progress', 'withdrawn', 'incomplete')
            AND total IS NULL AND letter_grade IS NULL AND grade_points IS NULL)
    )
);
CREATE INDEX enrollments_student_id ON enrollments (student_id);
CREATE INDEX enrollments_course_code ON enrollments (course_code);

COMMENT ON TABLE enrollments IS
    'One row per course attempt in a term, with marks; a retaken course has one row per attempt.';
COMMENT ON COLUMN enrollments.enrollment_id IS
    '-';
COMMENT ON COLUMN enrollments.student_id IS
    '-';
COMMENT ON COLUMN enrollments.course_code IS
    '-';
COMMENT ON COLUMN enrollments.term_id IS
    '-';
COMMENT ON COLUMN enrollments.midterm IS
    'Out of 20; empty until recorded.';
COMMENT ON COLUMN enrollments.coursework IS
    'Assignments, labs, quizzes, out of 20; empty until recorded.';
COMMENT ON COLUMN enrollments.final IS
    'Final exam, out of 60; empty until recorded.';
COMMENT ON COLUMN enrollments.total IS
    'Out of 100 (capped for retakes); empty until graded.';
COMMENT ON COLUMN enrollments.letter_grade IS
    'A+ to F; empty until graded.';
COMMENT ON COLUMN enrollments.grade_points IS
    '0 to 4. Term GPA = SUM(grade_points * credit_hours) / SUM(credit_hours) over graded courses.';
COMMENT ON COLUMN enrollments.status IS
    '''passed'', ''failed'', ''in_progress'' (this term), ''withdrawn'' or ''incomplete''. Only passed and failed count in the GPA.';

-- Excuses and improvement plans -------------------------------------------------------

CREATE TABLE excuses (
    excuse_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id     integer NOT NULL REFERENCES students (student_id),
    academic_year  text    NOT NULL CHECK (academic_year ~ '^[0-9]{4}/[0-9]{4}$'),
    excuse_type    text    NOT NULL CHECK (excuse_type IN ('term_excuse', 'year_excuse', 'suspension'))
);
CREATE INDEX excuses_student_id ON excuses (student_id);

COMMENT ON TABLE excuses IS
    'Approved excuses: time off that does not count against the student.';
COMMENT ON COLUMN excuses.excuse_id IS
    '-';
COMMENT ON COLUMN excuses.student_id IS
    '-';
COMMENT ON COLUMN excuses.academic_year IS
    'e.g. ''2024/2025'' (fall 2024 and spring 2025).';
COMMENT ON COLUMN excuses.excuse_type IS
    '''term_excuse'' (the spring term), ''year_excuse'' or ''suspension'' (enrollment suspended for the year).';

CREATE TABLE improvement_plans (
    improvement_plan_id  integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id           integer NOT NULL REFERENCES students (student_id),
    start_term_id        integer NOT NULL REFERENCES terms (term_id),
    evaluation_term_id   integer NOT NULL REFERENCES terms (term_id),
    outcome              text    NOT NULL CHECK (outcome IN ('in_progress', 'success', 'failed'))
);
CREATE INDEX improvement_plans_student_id ON improvement_plans (student_id);

COMMENT ON TABLE improvement_plans IS
    'Improvement plans for students at risk of dismissal.';
COMMENT ON COLUMN improvement_plans.improvement_plan_id IS
    '-';
COMMENT ON COLUMN improvement_plans.student_id IS
    '-';
COMMENT ON COLUMN improvement_plans.start_term_id IS
    'Term it started.';
COMMENT ON COLUMN improvement_plans.evaluation_term_id IS
    'Term at whose end it is judged.';
COMMENT ON COLUMN improvement_plans.outcome IS
    '''in_progress'', ''success'' or ''failed''.';

-- Study plans (next-term course choices a TA approves or rejects) ----------------------

CREATE TABLE plans (
    plan_id      integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    student_id   integer     NOT NULL REFERENCES students (student_id),
    term_id      integer     NOT NULL REFERENCES terms (term_id),
    created_at   timestamptz NOT NULL DEFAULT now(),
    status       text        NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewed_by  integer     REFERENCES tas (ta_id),
    reviewed_at  timestamptz,
    -- A pending plan has no reviewer yet; a reviewed one has both fields.
    CHECK (
        (status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status <> 'pending' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);
CREATE INDEX plans_student_id ON plans (student_id);

COMMENT ON TABLE plans IS
    'Next-term course plan sent to the advisor for approval.';
COMMENT ON COLUMN plans.plan_id IS
    '-';
COMMENT ON COLUMN plans.student_id IS
    '-';
COMMENT ON COLUMN plans.term_id IS
    'Term the plan is for (usually next term).';
COMMENT ON COLUMN plans.created_at IS
    'When it was sent.';
COMMENT ON COLUMN plans.status IS
    '''pending'', ''approved'' or ''rejected''.';
COMMENT ON COLUMN plans.reviewed_by IS
    'TA who reviewed it; empty while pending.';
COMMENT ON COLUMN plans.reviewed_at IS
    'When reviewed; empty while pending.';

CREATE TABLE plan_courses (
    plan_id      integer NOT NULL REFERENCES plans (plan_id) ON DELETE CASCADE,
    course_code  text    NOT NULL REFERENCES courses (course_code),
    PRIMARY KEY (plan_id, course_code)
);

COMMENT ON TABLE plan_courses IS
    'Courses in each plan.';
COMMENT ON COLUMN plan_courses.plan_id IS
    '-';
COMMENT ON COLUMN plan_courses.course_code IS
    '-';
