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
    UNIQUE (year, season)
);
-- At most one term can be the current one.
CREATE UNIQUE INDEX terms_one_current ON terms (is_current) WHERE is_current;

COMMENT ON TABLE terms IS
    'Academic terms (semesters). Each year has a fall and a spring term, and sometimes a short summer term.';
COMMENT ON COLUMN terms.term_id IS
    'Term number; a higher number is a later term.';
COMMENT ON COLUMN terms.year IS
    'Calendar year the term starts in, e.g. 2026 for fall 2026 and 2027 for spring 2027.';
COMMENT ON COLUMN terms.season IS
    'Which part of the year: ''fall'', ''spring'' or ''summer''.';
COMMENT ON COLUMN terms.is_current IS
    'True only for the term running now; "this term" means the row where is_current is true.';

-- Courses ------------------------------------------------------------------------------

CREATE TABLE courses (
    course_code          text    PRIMARY KEY CHECK (course_code ~ '^(CS|IS|BS|H) [1-4][0-9]{2}$'),
    title_en             text    NOT NULL,
    title_ar             text    NOT NULL,
    credit_hours         integer NOT NULL CHECK (credit_hours BETWEEN 1 AND 6),
    level                integer NOT NULL CHECK (level BETWEEN 1 AND 4),
    program              text    NOT NULL CHECK (program IN ('CS', 'IS', 'both')),
    is_required          boolean NOT NULL,
    min_passed_hours     integer NOT NULL DEFAULT 0 CHECK (min_passed_hours >= 0)
);

COMMENT ON TABLE courses IS
    'Every course in the Computer Science (CS) and Information Systems (IS) study plans.';
COMMENT ON COLUMN courses.course_code IS
    'Course code: department letters, a space, then three digits, e.g. ''CS 201''. CS = computer science, IS = information systems, BS = basic sciences (maths, physics), H = humanities. The first digit is the course level.';
COMMENT ON COLUMN courses.title_en IS
    'Course name in English, e.g. ''Data Structures''.';
COMMENT ON COLUMN courses.title_ar IS
    'Course name in Arabic, e.g. ''هياكل البيانات''.';
COMMENT ON COLUMN courses.credit_hours IS
    'Credit hours the course is worth: 3 for most courses, 2 for humanities (H) courses.';
COMMENT ON COLUMN courses.level IS
    'Course level from 1 to 4 (first digit of the code); level 1 courses are usually taken in the first year.';
COMMENT ON COLUMN courses.program IS
    'Which study plan the course belongs to: ''CS'', ''IS'' or ''both''.';
COMMENT ON COLUMN courses.is_required IS
    'True if every student in the program must pass it; false if it is an elective the student may choose.';
COMMENT ON COLUMN courses.min_passed_hours IS
    'Credit hours a student must have passed before taking the course; 0 for most courses, 95 for graduation project 1.';

-- Prerequisites ------------------------------------------------------------------------

CREATE TABLE prerequisites (
    course_code           text NOT NULL REFERENCES courses (course_code),
    required_course_code  text NOT NULL REFERENCES courses (course_code),
    PRIMARY KEY (course_code, required_course_code),
    CHECK (course_code <> required_course_code)
);

COMMENT ON TABLE prerequisites IS
    'Courses that must be passed before another course can be taken. A course with no rows here has no prerequisite course.';
COMMENT ON COLUMN prerequisites.course_code IS
    'The course that has the prerequisite.';
COMMENT ON COLUMN prerequisites.required_course_code IS
    'The course that must be passed first.';

-- Credit-hour limits -------------------------------------------------------------------

CREATE TABLE credit_rules (
    term_type         text         NOT NULL CHECK (term_type IN ('regular', 'summer')),
    gpa_from          numeric(3,2) NOT NULL CHECK (gpa_from >= 0),
    gpa_to            numeric(3,2) NOT NULL CHECK (gpa_to > gpa_from),
    max_credit_hours  integer      NOT NULL CHECK (max_credit_hours > 0),
    PRIMARY KEY (term_type, gpa_from)
);

COMMENT ON TABLE credit_rules IS
    'Most credit hours a student may register in one term, depending on cumulative GPA. A new student in their first term may register up to 21 hours. The minimum in a regular term is 9 hours.';
COMMENT ON COLUMN credit_rules.term_type IS
    '''regular'' for fall and spring terms, ''summer'' for the summer term.';
COMMENT ON COLUMN credit_rules.gpa_from IS
    'Lowest cumulative GPA of this band (included).';
COMMENT ON COLUMN credit_rules.gpa_to IS
    'Upper end of this band (not included): the rule applies when gpa_from <= GPA < gpa_to. The top band ends at 4.01 so a GPA of exactly 4.00 fits.';
COMMENT ON COLUMN credit_rules.max_credit_hours IS
    'Most credit hours allowed in one term for a GPA in this band.';

-- Grading scale ------------------------------------------------------------------------

CREATE TABLE grading_scale (
    letter_grade  text         PRIMARY KEY,
    min_total     numeric(5,2) NOT NULL CHECK (min_total BETWEEN 0 AND 100),
    points        numeric(2,1) NOT NULL CHECK (points BETWEEN 0 AND 4)
);

COMMENT ON TABLE grading_scale IS
    'How a total mark out of 100 becomes a letter grade and grade points. F (below 50) is a fail; every other letter is a pass.';
COMMENT ON COLUMN grading_scale.letter_grade IS
    'Letter grade, from ''A+'' (best) down to ''F'' (fail).';
COMMENT ON COLUMN grading_scale.min_total IS
    'Lowest total mark out of 100 that earns this letter grade.';
COMMENT ON COLUMN grading_scale.points IS
    'Grade points for this letter, from 4.0 for A+ to 0 for F; used to compute the GPA.';

-- Teaching assistants (academic advisors) ----------------------------------------------

CREATE TABLE tas (
    ta_id  integer PRIMARY KEY,
    name   text    NOT NULL
);

COMMENT ON TABLE tas IS
    'Teaching assistants who act as academic advisors and review students'' study plans.';
COMMENT ON COLUMN tas.ta_id IS
    'Teaching assistant ID.';
COMMENT ON COLUMN tas.name IS
    'Teaching assistant''s full name.';

-- Students -----------------------------------------------------------------------------

CREATE TABLE students (
    student_id      integer      PRIMARY KEY,
    name_ar         text         NOT NULL,
    name_en         text         NOT NULL,
    program         text         NOT NULL CHECK (program IN ('CS', 'IS')),
    level           integer      NOT NULL CHECK (level BETWEEN 1 AND 4),
    cumulative_gpa  numeric(3,2) CHECK (cumulative_gpa BETWEEN 0 AND 4),
    advisor_ta_id   integer      NOT NULL REFERENCES tas (ta_id)
);
CREATE INDEX students_advisor_ta_id ON students (advisor_ta_id);

COMMENT ON TABLE students IS
    'Students. Rows are already limited to the student asking (or a TA''s advisees), so queries never need to filter by student.';
COMMENT ON COLUMN students.student_id IS
    'Student ID.';
COMMENT ON COLUMN students.name_ar IS
    'Student''s full name in Arabic.';
COMMENT ON COLUMN students.name_en IS
    'Student''s full name in English letters.';
COMMENT ON COLUMN students.program IS
    'Study program: ''CS'' (computer science) or ''IS'' (information systems).';
COMMENT ON COLUMN students.level IS
    'Year of study, 1 to 4.';
COMMENT ON COLUMN students.cumulative_gpa IS
    'Cumulative GPA from 0.00 to 4.00 over all graded courses so far; empty for a new student with no grades yet.';
COMMENT ON COLUMN students.advisor_ta_id IS
    'The teaching assistant who advises this student.';

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
    'One row per course a student registered in a term, with the marks. A course taken again after failing or to improve the grade has one row per attempt.';
COMMENT ON COLUMN enrollments.enrollment_id IS
    'Row ID.';
COMMENT ON COLUMN enrollments.student_id IS
    'The student who took the course.';
COMMENT ON COLUMN enrollments.course_code IS
    'The course taken.';
COMMENT ON COLUMN enrollments.term_id IS
    'The term the course was taken in.';
COMMENT ON COLUMN enrollments.midterm IS
    'Midterm exam mark out of 20; empty until recorded.';
COMMENT ON COLUMN enrollments.coursework IS
    'Coursework mark (assignments, labs, quizzes) out of 20; empty until recorded.';
COMMENT ON COLUMN enrollments.final IS
    'Final exam mark out of 60; empty until recorded.';
COMMENT ON COLUMN enrollments.total IS
    'Total mark out of 100 (midterm + coursework + final, capped for repeated courses); empty until the course is graded.';
COMMENT ON COLUMN enrollments.letter_grade IS
    'Letter grade from A+ to F; empty until the course is graded.';
COMMENT ON COLUMN enrollments.grade_points IS
    'Grade points for the letter grade, 0 to 4. Term GPA = SUM(grade_points * credit_hours) / SUM(credit_hours) over graded courses.';
COMMENT ON COLUMN enrollments.status IS
    '''passed'', ''failed'', ''in_progress'' (this term, no grade yet), ''withdrawn'' or ''incomplete''. Only passed and failed rows count in the GPA.';

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
    'A student''s plan of courses for an upcoming term, sent to their advisor for approval.';
COMMENT ON COLUMN plans.plan_id IS
    'Plan ID.';
COMMENT ON COLUMN plans.student_id IS
    'The student the plan belongs to.';
COMMENT ON COLUMN plans.term_id IS
    'The term the plan is for (usually the term after the current one).';
COMMENT ON COLUMN plans.created_at IS
    'When the student sent the plan.';
COMMENT ON COLUMN plans.status IS
    '''pending'' (waiting for the advisor), ''approved'' or ''rejected''.';
COMMENT ON COLUMN plans.reviewed_by IS
    'The teaching assistant who approved or rejected the plan; empty while pending.';
COMMENT ON COLUMN plans.reviewed_at IS
    'When the plan was approved or rejected; empty while pending.';

CREATE TABLE plan_courses (
    plan_id      integer NOT NULL REFERENCES plans (plan_id) ON DELETE CASCADE,
    course_code  text    NOT NULL REFERENCES courses (course_code),
    PRIMARY KEY (plan_id, course_code)
);

COMMENT ON TABLE plan_courses IS
    'The courses listed in each plan.';
COMMENT ON COLUMN plan_courses.plan_id IS
    'The plan.';
COMMENT ON COLUMN plan_courses.course_code IS
    'A course the student plans to take.';
