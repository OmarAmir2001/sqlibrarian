"""Step 2.2 (8): remove training pairs that are too close to the test set.

If a training question is almost the same as a test question, the test
measures memory, not skill. Two checks:
  1. same SQL as a test reference query (after normalizing)
  2. question meaning too similar: cosine similarity above 0.9 with
     an embedding model that understands Arabic and English together (e5)

Only your own pairs need this; Spider and Ar-Spider use other databases.
The embedding model is settings.embedding_model. Runs on a laptop CPU in a few
minutes for about 4,000 questions (faster on a GPU).

Run:
    uv run python -m sqlibrarian.training.decontaminate
"""

import argparse
import json
from pathlib import Path

from sentence_transformers import SentenceTransformer

from sqlibrarian.config import get_settings
from sqlibrarian.log import configure_logging, get_logger
from sqlibrarian.training.sql_utils import normalize_sql

log = get_logger(__name__)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--train", type=Path, default=s.paths.own_pairs_reviewed)
    p.add_argument("--test", type=Path, default=s.paths.test_questions)
    p.add_argument("--out", type=Path, default=s.paths.own_pairs_clean)
    p.add_argument("--threshold", type=float, default=s.decontamination_threshold)
    p.add_argument("--model", default=s.embedding_model)
    args = p.parse_args()

    train, test = read_jsonl(args.train), read_jsonl(args.test)

    # 1. Same SQL as a test reference
    test_sql = {normalize_sql(t["reference_sql"]) for t in test}
    same_sql = {i for i, r in enumerate(train) if normalize_sql(r["reference_sql"]) in test_sql}

    # 2. Similar meaning. e5 models expect the "query: " prefix on every text.
    model = SentenceTransformer(args.model)

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
    too_close = {i for i, sim in enumerate(best) if sim > args.threshold}

    drop = same_sql | too_close
    with args.out.open("w", encoding="utf-8") as f:
        for i, r in enumerate(train):
            if i not in drop:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    log.info(
        "decontaminate.done", pairs_in=len(train), same_sql=len(same_sql),
        too_similar=len(too_close), dropped=len(drop), kept=len(train) - len(drop),
        out=str(args.out),
    )  # fmt: skip
    # Show the closest few, so you can judge whether the threshold is the right line.
    for i in best.argsort()[::-1][:5]:
        j = sims[i].argmax()
        log.info(
            "decontaminate.closest", similarity=round(float(best[i]), 3),
            train=train[i]["question"], test=test[j]["question"],
        )  # fmt: skip


if __name__ == "__main__":
    main()
