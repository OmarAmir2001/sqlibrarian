"""Score predictions by execution: does the model's SQL return the same rows as the
reference SQL?

    uv run python -m sqlibrarian.evaluation.score data/eval/predictions/*.jsonl

How one question is scored:
  invalid  the reply has no SQL, or it is not exactly one SELECT
  error    the SQL fails to run (bad column, syntax, over 3 seconds)
  wrong    it runs, but the rows differ from the reference for at least one student
  correct  same rows as the reference for every sample student

Each query runs as several different students (see evaluation/db.py), so a query is
only correct if it works for weak, strong, new and IS students alike. Rows are
compared as sorted lists, numbers rounded to 2 decimals; column names don't matter,
but the columns and their order do (standard execution accuracy).

Nothing is ever saved to the database: every query runs inside a transaction that is
rolled back, with a 3 second limit, and only single SELECTs are run at all.

Writes <run>.scores.jsonl (one line per question) next to each predictions file,
and prints a summary table. --mlflow also logs each run to MLflow
(MLFLOW_TRACKING_URI, e.g. DagsHub; needs `pip install mlflow`).
"""

import argparse
import json
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg

from sqlibrarian.config import get_settings
from sqlibrarian.evaluation.db import act_as_student, sample_students
from sqlibrarian.training.sql_utils import is_single_select


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


def score_file(
    cur: psycopg.Cursor, predictions: Path, questions: dict[str, dict], students: list[int]
) -> list[dict]:
    preds = [json.loads(x) for x in predictions.read_text("utf-8").splitlines() if x.strip()]
    results = []
    for p in preds:
        q = questions[p["id"]]
        result = {"id": p["id"], "lang": q["lang"], "difficulty": q["difficulty"], "sql": p["sql"]}
        if not p["sql"] or not is_single_select(p["sql"]):
            results.append({**result, "status": "invalid", "detail": "not a single SELECT"})
            continue
        status, detail = "correct", ""
        for sid in students:
            act_as_student(cur, sid)
            expected, ref_error = run(cur, q["reference_sql"])
            if ref_error:
                raise SystemExit(f"{q['id']}: the REFERENCE query fails: {ref_error}")
            got, error = run(cur, p["sql"])
            if error:
                status, detail = "error", error
                break
            if got != expected:
                status, detail = (
                    "wrong",
                    f"student {sid}: {len(got)} rows, expected {len(expected)}",
                )
                break
        results.append({**result, "status": status, "detail": detail})
    return results


def summarize(results: list[dict]) -> dict:
    def accuracy(rows: list[dict]) -> float:
        return round(100 * sum(r["status"] == "correct" for r in rows) / len(rows), 1)

    by = defaultdict(list)
    for r in results:
        by[f"lang_{r['lang']}"].append(r)
        by[f"difficulty_{r['difficulty']}"].append(r)
    counts = Counter(r["status"] for r in results)
    summary = {"n": len(results), "accuracy": accuracy(results)}
    summary |= {f"accuracy_{k}": accuracy(v) for k, v in sorted(by.items())}
    summary |= {f"{s}_pct": round(100 * counts[s] / len(results), 1) for s in ("invalid", "error")}
    return summary


def log_mlflow(meta: dict, summary: dict, scores_path: Path) -> None:
    try:
        import mlflow
    except ImportError as e:
        raise SystemExit("--mlflow needs the mlflow package: uv run --with mlflow ...") from e
    mlflow.set_experiment("sqlibrarian-baseline")
    with mlflow.start_run(run_name=meta.get("run")):
        mlflow.log_params({k: v for k, v in meta.items() if not isinstance(v, int | float)})
        mlflow.log_metrics(
            {k: v for k, v in (meta | summary).items() if isinstance(v, int | float)}
        )
        mlflow.log_artifact(str(scores_path))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("predictions", type=Path, nargs="+")
    p.add_argument("--questions", type=Path, default=Path("data/eval/dev_questions.jsonl"))
    p.add_argument("--students", type=int, nargs="*", help="default: 5 sample students")
    p.add_argument("--mlflow", action="store_true")
    args = p.parse_args()

    lines = args.questions.read_text("utf-8").splitlines()
    questions = {q["id"]: q for q in map(json.loads, filter(str.strip, lines))}
    table = []
    with psycopg.connect(get_settings().records_db_url) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '3s'")
        students = args.students or sample_students(cur)
        print(f"scoring as students {students}\n")
        for path in args.predictions:
            if path.name.endswith(".scores.jsonl"):
                continue
            results = score_file(cur, path, questions, students)
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

    header = f"{'run':38s} {'all':>6s} {'en':>6s} {'ar':>6s} {'mixed':>6s} {'invalid':>8s} "
    print(header + f"{'error':>6s} {'s/q':>6s} {'p95 s':>6s} {'GPU GB':>7s}")
    for name, s, m in table:
        print(
            f"{name:38s} {s['accuracy']:6.1f} {s.get('accuracy_lang_en', 0):6.1f} "
            f"{s.get('accuracy_lang_ar', 0):6.1f} {s.get('accuracy_lang_mixed', 0):6.1f} "
            f"{s['invalid_pct']:8.1f} {s['error_pct']:6.1f} {m.get('mean_seconds', 0):6.2f} "
            f"{m.get('p95_seconds', 0):6.2f} {m.get('peak_gpu_memory_gb') or 0:7.2f}"
        )


if __name__ == "__main__":
    main()
