# Writing the test set

The test set is what the project is graded on: **≥ 80% of these questions answered correctly
overall and ≥ 70% of the Arabic ones** by the fine-tuned model. It is never used for training,
and once it is frozen (`git tag testset-v1`) it doesn't change.

## Targets

| | Count | Notes |
|---|---|---|
| **English** (`en`) | 100 | the way students type: short, lower case, typos are fine |
| **Modern Standard Arabic** (`ar`) | 100 | فصحى: "ما معدلي التراكمي؟" |
| **Egyptian or mixed** (`mixed`) | 100 | عامية and/or Arabic with English words: "الـ GPA بتاعي كام؟" |
| Difficulty | about 40% easy, 35% medium, 25% hard | see below |
| Kind | about 25 of each of the 12 kinds | see below |

## The workflow

```bash
uv run python -m sqlibrarian.evaluation.test_set template   # once: creates data/eval/test_questions.csv
# ... write questions in Excel / Google Sheets ...
uv run python -m sqlibrarian.evaluation.test_set check      # as often as you like
uv run python -m sqlibrarian.evaluation.test_set build      # when check shows 0 errors
```

- **Excel:** save with *File → Save As → CSV UTF-8*. Plain "CSV" loses the Arabic.
- **Google Sheets:** *File → Download → Comma-separated values*, then replace the file.
- `check` prints every problem with its **row number** in the spreadsheet, and your progress toward
  100 / 100 / 100. The database must be running (`docker compose ... up -d`).
- After `build`, don't reorder or delete rows: ids (`test-001`, …) follow the row order.

## The columns

| Column | What to write |
|---|---|
| `lang` | `en`, `ar` or `mixed` |
| `difficulty` | `easy`, `medium` or `hard` |
| `kind` | one of the 12 kinds below |
| `question` | exactly what a student would type |
| `reference_sql` | the correct PostgreSQL query (rules below) |
| `notes` | anything for yourself; ignored by the tools |

## Rules for `reference_sql`

1. **One `SELECT`.** No `INSERT`, `UPDATE`, `DELETE`, no second statement.
2. **Never filter by student.** Write it for "the current student": no `student_id = …`,
   no placeholders. The database (row-level security) limits every table to the student asking.
   Joins such as `e.student_id = s.student_id` are fine.
3. **Find courses by their exact catalog name**, `c.title_en = 'Data Structures'` or
   `c.title_ar = 'هياكل البيانات'`. `check` rejects names that aren't in the catalog. If the student
   types a code ("CS 201"), filtering by `course_code` is fine.
4. **Return what the question asks for, no more.** Scoring compares the rows *and* the columns:
   "what's my GPA" → one column, `cumulative_gpa`; "which courses did I fail" → the courses.
   When in doubt, return the code and the name in the question's language
   (`title_en` for English, `title_ar` for Arabic).
5. **Use the house conventions** (the dev set, the seed and the plan checker use the same ones):
   - this term: `terms.is_current`; next term: the term after it
   - cumulative GPA: `students.cumulative_gpa`; a term GPA:
     `ROUND(SUM(e.grade_points * c.credit_hours) / SUM(c.credit_hours), 2)` over passed and failed courses
   - a grade in a course: the latest **graded** attempt (`status IN ('passed','failed')`,
     `ORDER BY term_id DESC LIMIT 1`)
   - "can I take X next term": a prerequisite counts as met if it is passed **or in progress**
   - most hours allowed: from `credit_rules` for the GPA band; **21** for a student with no GPA yet

`data/eval/schema.txt` describes every table and column; `data/eval/dev_questions.jsonl` has
50 worked examples of these conventions.

## The 12 kinds

Examples show the *kind*; don't copy them into the test set (copies of dev questions are flagged).

| `kind` | Asks about | Example of the kind |
|---|---|---|
| `grade` | a grade or mark in one course | "how much did I get on the OS final" |
| `gpa` | cumulative or term GPA | "معدلي في الترم اللي فات كام؟" |
| `failed` | failed or repeated courses | "ما المقررات التي أعدتها؟" |
| `credit_hours` | hours passed, registered, electives | "how many hours do I have left to graduate" |
| `courses_left` | required or elective courses still to take | "فاضلي ايه elective؟" |
| `available_next` | courses the student can take next term | "ما المقررات المتاحة لي الفصل القادم؟" |
| `prerequisites` | missing prerequisites for a named course | "what do I need before Computer Vision Systems" |
| `plan` | next-term plan: status, courses, hours | "الخطة بتاعتي فيها كام ساعة؟" |
| `this_term` | this term's courses, marks not recorded yet | "did my coursework mark in AI come out" |
| `compare_terms` | best / worst term, improvement | "هل تحسن معدلي هذا العام؟" |
| `profile` | advisor, program, level, credit limit | "انا في انهي level؟" |
| `other` | anything that fits none of the above | |

## Difficulty

- **easy**: one table, or one simple join, no grouping ("my GPA", "my advisor").
- **medium**: a join plus a filter or an aggregate ("hours passed in level 2", "GPA last term").
- **hard**: several steps: subqueries, `NOT IN` / `NOT EXISTS`, grouping and ranking
  ("courses I can take next term", "my best term", "required courses left").

## Writing good questions

- **Start from real questions** students ask you as a TA, in their words. That is what makes this
  set worth more than generated data.
- **Vary the wording** of the same need: "my gpa", "cumulative gpa?", "what's my average".
- **Include realistic mess:** typos, missing question marks, course nicknames the model can't know
  ("DS") are fair only if the SQL still has one clear answer; otherwise skip them.
- **Make every question answerable from the database.** `check` rejects a question no student has
  any answer to, and warns when every answer is only empty or zero.
- **Run `check` every 20 or 30 questions**, so mistakes don't pile up.
