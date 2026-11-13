"""Score predictions by execution: does the model's SQL return the same rows as the
reference SQL?

    uv run python -m sqlibrarian.evaluation.score data/eval/predictions/*.jsonl

How one question is scored:
  invalid  the reply has no SQL, or it is not exactly one SELECT
  error    the SQL fails to run (bad column, syntax, over 3 seconds)
  wrong    it runs, but the rows differ from the reference for at least one student
  correct  same rows as the reference for every sample student

Each query runs as several different students (see evaluation/db.py), so a query is
only correct if it works for weak, strong, new and IS students alike. When none of
them gets a meaningful answer (nobody took the course asked about), one more student
who does is added for that question, so empty never matches empty by luck. Rows are
compared as sorted lists, numbers rounded to 2 decimals; column names don't matter,
but the columns and their order do (standard execution accuracy).

Nothing is ever saved to the database: every query runs inside a transaction that is
rolled back, with a 3 second limit, and only single SELECTs are run at all.

Writes <run>.scores.jsonl (one line per question) next to each predictions file,
and prints a summary table. --mlflow also logs each run to MLflow
(MLFLOW_TRACKING_* in .env, e.g. DagsHub; run with `uv run --with mlflow`).
"""

import argparse
import json
import os
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

from sqlibrarian.evaluation.db import act_as_student, sample_students, show_variant
from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Lang, ScoreStatus
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.training.sql_utils import is_single_select

log = get_logger(__name__)

TABLE_LANGS = (Lang.EN, Lang.AR, Lang.MIXED)  # columns of the score table


def normalize(rows: list[tuple]) -> list[tuple]:
    def value(v):
        if isinstance(v, Decimal | float):
            return round(float(v), 2)
        if isinstance(v, str):
            return v.strip()
        return v

    return sorted((tuple(value(v) for v in r) for r in rows), key=repr)


def run(cur: psycopg.Cursor, sql: str) -> tuple[list[tuple] | None, str | None]:
    cur.execute("SAVEPOINT q")
    try:
        cur.execute(sql)
        return normalize(cur.fetchall()), None
    except psycopg.Error as e:
        return None, (e.diag.message_primary or type(e).__name__)
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT q")


def says_something(rows: list[tuple]) -> bool:
    """False for no rows, or only NULL / 0 / false values: an answer a wrong query
    could easily match by accident."""
    return any(v not in (None, 0, False) for r in rows for v in r)


def students_for(
    cur: psycopg.Cursor, q: dict, sample: list[int], cache: dict[str, list[int]]
) -> list[int]:
    """The sample students, plus one more if none of them gets a meaningful answer
    (e.g. nobody in the sample took the course asked about). Without that witness,
    an empty answer would match an empty wrong answer."""
    if q["id"] in cache:
        return cache[q["id"]]
    students = list(sample)

    def answer(sid: int) -> list[tuple]:
        act_as_student(cur, sid)
        rows, error = run(cur, q["reference_sql"])
        if error:
            raise SystemExit(f"{q['id']}: the REFERENCE query fails: {error}")
        return rows

    if not any(says_something(answer(s)) for s in sample):
        cur.execute("SELECT student_id FROM public.students ORDER BY student_id")
        everyone = [r[0] for r in cur.fetchall()]
        witness = next((s for s in everyone if says_something(answer(s))), None)
        witness = witness or next((s for s in everyone if answer(s)), None)
        if witness is None:
            log.warning(
                "score.question_tests_nothing", id=q["id"], reason="empty for every student"
            )
        else:
            students.append(witness)
    cache[q["id"]] = students
    return students


def judge(
    cur: psycopg.Cursor, sql: str, reference_sql: str, students: list[int]
) -> tuple[ScoreStatus, str]:
    """Is one predicted query right? The single definition used by evaluation (here) and
    by the GRPO reward (training/rewards.py)."""
    if not sql or not is_single_select(sql):
        return ScoreStatus.INVALID, "not a single SELECT"
    for sid in students:
        act_as_student(cur, sid)
        expected, ref_error = run(cur, reference_sql)
        if ref_error:
            raise SystemExit(f"the REFERENCE query fails: {ref_error}\n{reference_sql}")
        got, error = run(cur, sql)
        if error:
            return ScoreStatus.ERROR, error
        if got != expected:
            return ScoreStatus.WRONG, f"student {sid}: {len(got)} rows, expected {len(expected)}"
    return ScoreStatus.CORRECT, ""


def score_file(
    cur: psycopg.Cursor,
    predictions: Path,
    questions: dict[str, dict],
    sample: list[int],
    cache: dict[str, list[int]],
) -> list[dict]:
    preds = [json.loads(x) for x in predictions.read_text("utf-8").splitlines() if x.strip()]
    results = []
    for p in preds:
        q = questions[p["id"]]
        students = students_for(cur, q, sample, cache)
        status, detail = judge(cur, p["sql"], q["reference_sql"], students)
        results.append(
            {"id": p["id"], "lang": q["lang"], "difficulty": q["difficulty"], "sql": p["sql"],
             "status": status, "detail": detail}
        )  # fmt: skip
    return results


def summarize(results: list[dict]) -> dict:
    def accuracy(rows: list[dict]) -> float:
        return round(100 * sum(r["status"] == ScoreStatus.CORRECT for r in rows) / len(rows), 1)

    by = defaultdict(list)
    for r in results:
        by[f"lang_{r['lang']}"].append(r)
        by[f"difficulty_{r['difficulty']}"].append(r)
    counts = Counter(r["status"] for r in results)
    summary = {"n": len(results), "accuracy": accuracy(results)}
    summary |= {f"accuracy_{k}": accuracy(v) for k, v in sorted(by.items())}
    summary |= {
        f"{s}_pct": round(100 * counts[s] / len(results), 1)
        for s in (ScoreStatus.INVALID, ScoreStatus.ERROR)
    }
    return summary


def log_mlflow(meta: dict, summary: dict, scores_path: Path) -> None:
    try:
        import mlflow
    except ImportError as e:
        raise SystemExit("--mlflow needs the mlflow package: uv run --with mlflow ...") from e
    settings = get_settings()
    if not settings.mlflow_tracking_uri:
        raise SystemExit("--mlflow needs MLFLOW_TRACKING_URI (and username/password) in .env")
    # MLflow reads its login from environment variables; copy them from .env.
    os.environ["MLFLOW_TRACKING_USERNAME"] = settings.mlflow_tracking_username or ""
    os.environ["MLFLOW_TRACKING_PASSWORD"] = settings.mlflow_tracking_password or ""
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(settings.mlflow_baseline_experiment)
    with mlflow.start_run(run_name=meta.get("run")):
        mlflow.log_params({k: v for k, v in meta.items() if not isinstance(v, int | float)})
        mlflow.log_metrics(
            {k: v for k, v in (meta | summary).items() if isinstance(v, int | float)}
        )
        mlflow.log_artifact(str(scores_path))


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("predictions", type=Path, nargs="+")
    p.add_argument("--questions", type=Path, help="default: the dev questions (of the variant)")
    p.add_argument(
        "--variant", choices=["heldout"],
        help="score predictions written for the renamed held-out schema (schema_heldout.txt)",
    )  # fmt: skip
    p.add_argument("--students", type=int, nargs="*", help="default: 5 sample students")
    p.add_argument("--mlflow", action="store_true")
    args = p.parse_args()

    paths = get_settings().paths
    default_questions = paths.dev_questions_heldout if args.variant else paths.dev_questions
    lines = (args.questions or default_questions).read_text("utf-8").splitlines()
    questions = {q["id"]: q for q in map(json.loads, filter(str.strip, lines))}
    table = []
    with psycopg.connect(get_settings().records_dsn) as conn, conn.cursor() as cur:
        timeout = get_settings().statement_timeout
        cur.execute("SELECT set_config('statement_timeout', %s, true)", (timeout,))
        students = args.students or sample_students(cur)
        if args.variant:
            from sqlibrarian.training.schema_variants import HELDOUT

            act_as_student(cur, students[0])  # the per-student views come first
            show_variant(cur, HELDOUT)
        witnesses: dict[str, list[int]] = {}
        log.info("score.start", students=students, note="+1 student for questions they can't test")
        for path in args.predictions:
            if path.name.endswith(".scores.jsonl"):
                continue
            results = score_file(cur, path, questions, students, witnesses)
            summary = summarize(results)
            scores_path = path.with_name(path.name.removesuffix(".jsonl") + ".scores.jsonl")
            scores_path.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), "utf-8"
            )
            meta_path = path.with_name(path.name.removesuffix(".jsonl") + ".meta.json")
            meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
            table.append((meta.get("run", path.stem), summary, meta))
            if args.mlflow:
                log_mlflow(meta, summary, scores_path)
        conn.rollback()

    # The score table is this command's output (stdout), not a log line.
    header = f"{'run':38s} {'all':>6s} {'en':>6s} {'ar':>6s} {'mixed':>6s} {'invalid':>8s} "
    print(header + f"{'error':>6s} {'s/q':>6s} {'p95 s':>6s} {'GPU GB':>7s}")
    for name, s, m in table:
        print(
            f"{name:38s} {s['accuracy']:6.1f} "
            + "".join(f"{s.get(f'accuracy_lang_{lang}', 0):6.1f} " for lang in TABLE_LANGS)
            + f"{s['invalid_pct']:8.1f} {s['error_pct']:6.1f} {m.get('mean_seconds', 0):6.2f} "
            f"{m.get('p95_seconds', 0):6.2f} {m.get('peak_gpu_memory_gb') or 0:7.2f}"
        )


if __name__ == "__main__":
    main()
