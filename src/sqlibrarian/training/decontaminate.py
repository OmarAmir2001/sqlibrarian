"""Step 2.2 (8): remove training pairs that are too close to the test set.

If a training question is almost the same as a test question, the test
measures memory, not skill. Two checks:
  1. same SQL as a test reference query (after normalizing)
  2. question meaning too similar: cosine similarity above 0.9 with
     multilingual-e5-large, which understands Arabic and English together

Only your own pairs need this; Spider and Ar-Spider use other databases.
Runs on a laptop CPU in a few minutes for about 4,000 questions (faster on a GPU).

Run:
    uv run python -m sqlibrarian.training.decontaminate
"""

import argparse
import json
from pathlib import Path

from sentence_transformers import SentenceTransformer

from sqlibrarian.training.sql_utils import normalize_sql


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--train", type=Path, default=Path("data/training/own_pairs_reviewed.jsonl"))
    p.add_argument("--test", type=Path, default=Path("data/eval/test_questions.jsonl"))
    p.add_argument("--out", type=Path, default=Path("data/training/own_pairs_clean.jsonl"))
    p.add_argument("--threshold", type=float, default=0.9)
    args = p.parse_args()

    train, test = read_jsonl(args.train), read_jsonl(args.test)

    # 1. Same SQL as a test reference
    test_sql = {normalize_sql(t["reference_sql"]) for t in test}
    same_sql = {i for i, r in enumerate(train) if normalize_sql(r["reference_sql"]) in test_sql}

    # 2. Similar meaning. e5 models expect the "query: " prefix on every text.
    model = SentenceTransformer("intfloat/multilingual-e5-large")

    def enc(rows: list[dict]):
        return model.encode(
            [f"query: {r['question']}" for r in rows],
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=True,
        )

    train_vecs, test_vecs = enc(train), enc(test)
    sims = train_vecs @ test_vecs.T  # normalized vectors: dot product = cosine
    best = sims.max(axis=1)
    too_close = {i for i, s in enumerate(best) if s > args.threshold}

    drop = same_sql | too_close
    with args.out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(train):
            if i not in drop:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(
        f"{len(train)} pairs in; dropped {len(same_sql)} same-SQL, {len(too_close)} too similar "
        f"({len(drop)} in total); {len(train) - len(drop)} written to {args.out}"
    )
    # Show the closest few, so you can judge whether 0.9 is the right line.
    for i in best.argsort()[::-1][:5]:
        j = sims[i].argmax()
        print(f"  {best[i]:.3f}  train: {train[i]['question']!r}")
        print(f"         test:  {test[j]['question']!r}")


if __name__ == "__main__":
    main()
