"""Drop training examples that are too long for the model's context during training.

Each example is measured the way the model will see it: the chat messages are
rendered with the model's own chat template and counted with its tokenizer.
Examples over --max-tokens (default 2,048) are dropped. Most Spider examples are
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

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-tokens", type=int, default=2048)
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
        print(
            f"{split}: {len(rows)} in, {dropped} dropped (> {args.max_tokens} tokens), "
            f"{len(kept[split])} kept; p95 {p95}, longest {lengths[-1] if lengths else 0}"
        )

    kept.save_to_disk(str(args.out))
    print(f"saved to {args.out}")


if __name__ == "__main__":
    main()
