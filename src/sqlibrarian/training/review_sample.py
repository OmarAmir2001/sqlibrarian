"""Step 2.2 (7): review a random sample by hand, then apply your decisions.

1) Make the sheet:
    uv run python -m sqlibrarian.training.review_sample make
   Open data/training/review_sample.csv in Excel or Google Sheets.
   For each row, put y or n in `correct`. If n and you can fix the SQL,
   write the fixed SQL in `fix`.

2) Apply it and see the error rate:
    uv run python -m sqlibrarian.training.review_sample apply
   Writes data/training/own_pairs_reviewed.jsonl: wrong rows without a fix are
   dropped, fixed rows get the new SQL. More than about 10% wrong means:
   improve the prompt in generate_pairs.py and regenerate, don't hand-fix.
"""

import csv
import json
import random
import sys
from pathlib import Path

FILES = [Path("data/training/own_pairs.jsonl"), Path("data/training/own_pairs_arabic.jsonl")]
SHEET = Path("data/training/review_sample.csv")
OUT = Path("data/training/own_pairs_reviewed.jsonl")


def load_all() -> list[dict]:
    rows = []
    for f in FILES:
        if f.exists():
            rows += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def make(n: int = 200) -> None:
    random.seed(7)
    rows = load_all()
    sample = random.sample(rows, min(n, len(rows)))
    # utf-8-sig makes Excel show Arabic correctly
    with SHEET.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["id", "lang", "question", "reference_sql", "correct", "fix"]
        )
        w.writeheader()
        for r in sample:
            w.writerow(
                {
                    "id": r["id"],
                    "lang": r["lang"],
                    "question": r["question"],
                    "reference_sql": r["reference_sql"],
                    "correct": "",
                    "fix": "",
                }
            )
    print(f"wrote {SHEET}: review {len(sample)} rows")


def apply() -> None:
    with SHEET.open(encoding="utf-8-sig") as f:
        verdicts = {r["id"]: r for r in csv.DictReader(f)}
    reviewed = [v for v in verdicts.values() if v["correct"].strip().lower() in ("y", "n")]
    wrong = [v for v in reviewed if v["correct"].strip().lower() == "n"]
    print(
        f"reviewed {len(reviewed)}, wrong {len(wrong)} "
        f"({100 * len(wrong) / max(len(reviewed), 1):.1f}%)"
    )

    kept = 0
    with OUT.open("w", encoding="utf-8") as f:
        for r in load_all():
            v = verdicts.get(r["id"])
            if v and v["correct"].strip().lower() == "n":
                if not v["fix"].strip():
                    continue  # wrong and not fixed: drop
                r["reference_sql"] = v["fix"].strip()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1
    print(f"wrote {OUT}: {kept} pairs")


if __name__ == "__main__":
    {"make": make, "apply": apply}[sys.argv[1]]()
