"""Drop training examples that are too long for the model's context during training.

Each example is measured the way the model will see it: the chat messages are
rendered with the model's own chat template and counted with its tokenizer.
Examples over --max-tokens (settings.max_train_tokens, 2,048) are dropped. Most Spider examples are
far shorter; the long ones are big schemas, and cutting them off mid-schema
would teach the model from a broken prompt.

Works on any dataset saved with save_to_disk() that has a "messages" column
(for example the output of spider_data.py). Every split is filtered.

Run (on Kaggle or a laptop; downloads only the tokenizer, not the model):
    python -m sqlibrarian.training.filter_length \
        --data data/training/public --out data/training/public_filtered
"""

import argparse
from pathlib import Path

from datasets import DatasetDict, load_from_disk
from transformers import AutoTokenizer

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, default=s.paths.public)
    p.add_argument("--out", type=Path, default=s.paths.public_filtered)
    p.add_argument("--model", default=s.base_model, help="whose tokenizer counts the tokens")
    p.add_argument("--max-tokens", type=int, default=s.max_train_tokens)
    args = p.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model)

    def count(example: dict) -> dict:
        # Render to text first, then count: the same on every transformers version.
        text = tokenizer.apply_chat_template(example["messages"], tokenize=False)
        return {"n_tokens": len(tokenizer(text, add_special_tokens=False)["input_ids"])}

    ds = load_from_disk(str(args.data))
    if not isinstance(ds, DatasetDict):
        ds = DatasetDict({"train": ds})

    kept = DatasetDict()
    for split, rows in ds.items():
        rows = rows.map(count, desc=f"counting {split}")
        lengths = sorted(rows["n_tokens"])
        p95 = lengths[int(0.95 * (len(lengths) - 1))] if lengths else 0
        kept[split] = rows.filter(lambda ex: ex["n_tokens"] <= args.max_tokens)
        dropped = len(rows) - len(kept[split])
        log.info(
            "length_filter.split", split=split, examples_in=len(rows), dropped=dropped,
            kept=len(kept[split]), max_tokens=args.max_tokens, p95_tokens=p95,
            longest_tokens=lengths[-1] if lengths else 0,
        )  # fmt: skip

    kept.save_to_disk(str(args.out))
    log.info("length_filter.saved", out=str(args.out))


if __name__ == "__main__":
    main()
