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
from enum import StrEnum

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

SAMPLE_SIZE = 200
SAMPLE_SEED = 7
COLUMNS = ["id", "lang", "question", "reference_sql", "correct", "fix"]


class Verdict(StrEnum):
    """What the reviewer types in the `correct` column."""

    YES = "y"
    NO = "n"


def verdict(row: dict) -> Verdict | None:
    value = row["correct"].strip().lower()
    return Verdict(value) if value in iter(Verdict) else None


def load_all() -> list[dict]:
    paths = get_settings().paths
    rows = []
    for f in (paths.own_pairs, paths.own_pairs_arabic):
        if f.exists():
            rows += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def make() -> None:
    sheet = get_settings().paths.review_sheet
    rows = load_all()
    sample = random.Random(SAMPLE_SEED).sample(rows, min(SAMPLE_SIZE, len(rows)))
    # utf-8-sig makes Excel show Arabic correctly
    with sheet.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in sample:
            w.writerow({**{c: r.get(c, "") for c in COLUMNS[:4]}, "correct": "", "fix": ""})
    log.info("review.sheet_written", path=str(sheet), rows=len(sample))


def apply() -> None:
    paths = get_settings().paths
    with paths.review_sheet.open(encoding="utf-8-sig") as f:
        verdicts = {r["id"]: r for r in csv.DictReader(f)}
    reviewed = [v for v in verdicts.values() if verdict(v)]
    wrong = [v for v in reviewed if verdict(v) is Verdict.NO]
    log.info(
        "review.result", reviewed=len(reviewed), wrong=len(wrong),
        wrong_pct=round(100 * len(wrong) / max(len(reviewed), 1), 1),
    )  # fmt: skip

    kept = 0
    with paths.own_pairs_reviewed.open("w", encoding="utf-8") as f:
        for r in load_all():
            v = verdicts.get(r["id"])
            if v and verdict(v) is Verdict.NO:
                if not v["fix"].strip():
                    continue  # wrong and not fixed: drop
                r["reference_sql"] = v["fix"].strip()
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1
    log.info("review.applied", path=str(paths.own_pairs_reviewed), pairs=kept)


if __name__ == "__main__":
    configure_logging()
    commands = {"make": make, "apply": apply}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("usage: python -m sqlibrarian.training.review_sample make|apply")
    commands[sys.argv[1]]()
