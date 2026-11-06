"""Step 2.1: turn Spider and Ar-Spider into chat examples.

Every question in both datasets has a `db_id`: the name of the database it
was asked about. tables.json describes every database. We build a schema text
for each db_id once, then attach the right one to every question.

Run (on Kaggle, after attaching your Ar-Spider dataset):
    python -m sqlibrarian.training.spider_data \
        --arspider-dir /kaggle/input/<your-ar-spider-dataset> \
        --out data/training/public
"""

import argparse
import json
from pathlib import Path

from datasets import Dataset, DatasetDict, load_dataset

from sqlibrarian.prompt import build_messages


def schema_to_text(db: dict) -> str:
    """Describe one Spider database in the same layout as your own schema.txt:
    one line per table, then the foreign keys."""
    tables = db["table_names_original"]
    columns = db["column_names_original"]  # [[table_index, column_name], ...]; index 0 is [-1, "*"]
    types = db["column_types"]

    lines = []
    for t_idx, table in enumerate(tables):
        cols = [
            f"{name} {types[c_idx].upper()}"
            for c_idx, (owner, name) in enumerate(columns)
            if owner == t_idx
        ]
        lines.append(f"TABLE {table} ({', '.join(cols)})")

    for a, b in db.get("foreign_keys", []):
        ta, ca = columns[a]
        tb, cb = columns[b]
        lines.append(f"-- {tables[ta]}.{ca} references {tables[tb]}.{cb}")
    return "\n".join(lines)


def load_schemas(tables_json: Path) -> dict[str, str]:
    """db_id -> schema text, for all 166 databases."""
    dbs = json.loads(tables_json.read_text(encoding="utf-8"))
    return {db["db_id"]: schema_to_text(db) for db in dbs}


def to_examples(rows, schemas: dict[str, str], source: str) -> list[dict]:
    """Attach the right schema to every question. Rows whose db_id has no
    schema are counted and skipped, so you notice if files don't match."""
    examples, missing = [], 0
    for r in rows:
        schema = schemas.get(r["db_id"])
        if schema is None:
            missing += 1
            continue
        examples.append(
            {
                "messages": build_messages(schema, r["question"], r["query"]),
                "db_id": r["db_id"],
                "reference_sql": r["query"],
                "source": source,
            }
        )
    print(f"{source}: {len(examples)} examples, {missing} skipped (unknown db_id)")
    return examples


def read_json_rows(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    print(f"{path.name}: {len(rows)} rows, keys = {sorted(rows[0].keys())}")
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--arspider-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, default=Path("data/training/public"))
    args = p.parse_args()

    # Ar-Spider's zip may unpack into a sub-folder: find the files wherever they are.
    def find(name: str) -> Path:
        hits = list(args.arspider_dir.rglob(name))
        if not hits:
            raise SystemExit(f"{name} not found under {args.arspider_dir}")
        return hits[0]

    schemas = load_schemas(find("tables.json"))
    print(f"schemas for {len(schemas)} databases")
    print("example schema:\n", next(iter(schemas.values()))[:400], "\n")

    spider = load_dataset("xlangai/spider")  # splits: train, validation
    ar_train = read_json_rows(find("Ar_train_spider.json"))
    ar_dev = read_json_rows(find("Ar_dev_spider.json"))

    train = to_examples(spider["train"], schemas, "spider") + to_examples(
        ar_train, schemas, "ar-spider"
    )
    dev = to_examples(ar_dev, schemas, "ar-spider-dev")  # kept for reporting, never trained on

    ds = DatasetDict({"train": Dataset.from_list(train), "ar_dev": Dataset.from_list(dev)})
    ds.save_to_disk(str(args.out))
    print(f"saved to {args.out}")
    print(ds["train"][0]["messages"][1]["content"][:300])


if __name__ == "__main__":
    main()
