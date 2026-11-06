# Baseline: four models before fine-tuning

**Date:** 2026-11-06 · **Phase:** P0 (gate: choose the base model)
**Decision:** fine-tune **`Qwen/Qwen2.5-Coder-1.5B-Instruct`**; `Qwen2.5-Coder-3B-Instruct` stays the fallback.

## Result

No off-the-shelf model can answer questions about this database: the best runs get
**2 of 50 dev questions right (4%)**, against a target of 80% after training.
The three Qwen2.5-Coder runs are tied (one question is worth 2 points, so the gaps are noise);
Qwen3.5-2B gets none right and is the slowest.

| Run | Correct | English | MSA Arabic | Egyptian / mixed | Invalid SQL | SQL errors | Seconds / question | p95 seconds | Peak GPU memory |
|---|---|---|---|---|---|---|---|---|---|
| Qwen2.5-Coder-1.5B + 3 examples | **4%** (2) | 5% | 0% | 7% | 0% | 26% | 1.97 | 3.13 | 3.5 GB |
| Qwen2.5-Coder-1.5B | **4%** (2) | 0% | 0% | 13% | 0% | 36% | 2.44 | 4.52 | 3.5 GB |
| Qwen2.5-Coder-3B | **4%** (2) | 5% | 7% | 0% | 6% | 36% | 3.84 | 6.84 | 6.6 GB |
| Qwen3.5-2B | **0%** | 0% | 0% | 0% | 12% | 46% | 5.94 | 13.83 | 4.1 GB |

No model answered any of the 12 hard questions (courses left, courses available next term,
best term, missing prerequisites).

## Why they fail

The failures are about **this schema and its conventions**, not about SQL in general, which is
what supervised fine-tuning on our own question/SQL pairs is meant to fix.

| Failure | Example from the runs | How often (of 50 queries) |
|---|---|---|
| Filters by the student itself, although the schema says rows are already limited to the student asking | `WHERE student_id = :student_id`, a `your_student_id` column, `current_user_id()` | 1.5B: 16 · 3B: 21 · Qwen3.5: 17 · **1.5B + examples: 5** |
| Uses a column that doesn't exist or is on another table | `e.credit_hours` (it is `courses.credit_hours`), `t.total` | 7–15 per run |
| Guesses course codes instead of matching names | `course_code = 'CS 201'` for *Calculus* and for *Discrete Mathematics* | 5–10 per run |
| Computes GPA instead of reading it | `AVG(grade_points)`, ignoring credit hours and `students.cumulative_gpa` | up to 3 |
| Breaks the query with a placeholder | `:student_id` is not PostgreSQL, so the 3B fails with a syntax error | 3B: 11 |
| Gets multi-step logic wrong | "required courses left" answered with the courses already **passed** | most hard questions |

The three examples in the prompt had the clearest effect of anything tried: they cut the
self-filtering from 16 queries to 5 and errors from 36% to 26%, and made answers shorter
and faster. Training data should therefore show many queries that rely on row-level security
instead of filtering.

## Decision

**Qwen2.5-Coder-1.5B-Instruct**, as planned in the SDD:

- **Accuracy:** tied with the 3B, so the bigger model buys nothing yet.
- **Memory:** 3.5 GB against 6.6 GB, which leaves room on a 16 GB T4 for vLLM's cache in serving
  and for larger batches in LoRA training.
- **Latency:** p95 of 4.5 s for generation alone, already under the 5 s target with plain
  `transformers`; vLLM with prefix caching (P5) is expected to cut this a lot, because the
  1,434-token schema is identical in every request.
- **Fallback:** the 3B, if the fine-tuned 1.5B stalls well below 80% on the test set.
- **Not chosen:** Qwen3.5-2B, last on accuracy, speed and valid SQL.

## How it was measured

- **Questions:** the 50 dev questions in `data/eval/dev_questions.jsonl`: 20 English,
  15 Modern Standard Arabic, 15 Egyptian Arabic or mixed; 20 easy, 18 medium, 12 hard.
  They are a development set, not the test set: the 300-question test set (P1) is hand-written
  and is what the 80% target is measured on.
- **Prompt:** `prompt.py`, version `v1`: one system line, the schema text exported from the
  database comments (`data/eval/schema.txt`, 1,434 tokens), the question. The few-shot run adds
  three example pairs that are not in the dev set.
- **Generation:** Kaggle, one Tesla T4, float16, Hugging Face `transformers`, greedy decoding,
  up to 256 new tokens (`notebooks/sqlibrarian-baseline.ipynb`, `evaluation/generate.py`).
  Times are generation only, one question at a time.
- **Scoring:** execution accuracy (`evaluation/score.py`), run on the local `records_db`
  (500 fake students). A prediction is **correct** only if it returns the same rows as the
  reference query for **every** sample student: the weakest and strongest GPA, a new student
  with no grades, an IS student and a student with an approved plan. Row-level security is
  imitated with per-student views. Rows are compared as sorted lists, numbers rounded to
  2 decimals; the columns and their order must match. **Invalid** = no single SELECT;
  **error** = the database rejects it.
- **One scoring fix during the analysis:** on 7 questions none of the five students had a
  meaningful answer (for example, none had taken *Data Structures*), so an empty wrong answer
  matched the empty right one; one prediction was counted correct that way. The scorer now
  adds one student who does have an answer for such questions. All numbers above are after
  the fix.

## Limits

- 50 questions are too few to separate models closer than about 10 points.
- Scoring is strict about columns: a query that returns the right courses with Arabic titles
  instead of English ones counts as wrong. This understates all models equally.
- Latency here is not serving latency: no batching, no vLLM, no guard or database time.

## Reproduce

```bash
# Kaggle (GPU T4): run notebooks/sqlibrarian-baseline.ipynb, download baseline_predictions.zip
unzip baseline_predictions.zip -d data/eval/
docker compose --env-file .env -f infra/docker-compose.yml up -d
uv run python db/seed.py      # same data every time (fixed seed)
uv run python -m sqlibrarian.evaluation.score data/eval/predictions/*.jsonl
```
