"""Step 3: turn question/SQL pairs into training examples, and mix the training set.

Every example is a conversational *prompt-completion* pair built with the one
build_messages(), so training sees exactly the prompt the API will send:
    prompt     = [system, user (schema + question)]
    completion = [assistant (the SQL)]
TRL then computes the loss on the completion only (the SQL), not on the schema.

Laptop (own pairs are made and validated there; needs HF_TOKEN in .env):
    uv run --with datasets python -m sqlibrarian.training.format push-own
Pushes the best version of your own pairs that exists (decontaminated > reviewed >
validated + Arabic rewrites) as the private dataset config "own".

Kaggle: sft.py calls load_sft_data(), which reads configs "public" (Spider + Ar-Spider,
from the data notebook) and "own" and mixes them.
"""

import argparse
import json
from pathlib import Path

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.hub import dataset_repo
from sqlibrarian.helpers.lineage import data_fingerprint, git_commit
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.helpers.prompt import PROMPT_VERSION, build_messages

log = get_logger(__name__)

OWN_CONFIG = "own"
PUBLIC_CONFIG = "public"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def own_pairs_source() -> tuple[str, list[Path]]:
    """The most finished version of the own pairs that exists, best first."""
    p = get_settings().paths
    if p.own_pairs_clean.exists():
        return "decontaminated", [p.own_pairs_clean]
    if p.own_pairs_reviewed.exists():
        return "reviewed", [p.own_pairs_reviewed]
    files = [f for f in (p.own_pairs, p.own_pairs_arabic) if f.exists()]
    if not files:
        raise SystemExit(f"no own pairs found: run validate_pairs first ({p.own_pairs})")
    return "validated", files


def to_prompt_completion(messages: list[dict]) -> dict:
    """[system, user, assistant] -> prompt [system, user], completion [assistant]."""
    if not messages or messages[-1]["role"] != "assistant":
        raise ValueError("the last message must be the assistant's answer")
    return {"prompt": messages[:-1], "completion": messages[-1:]}


def own_examples(rows: list[dict], schema: str) -> list[dict]:
    """Own pairs -> prompt/completion examples, keeping the fields used for splitting."""
    out = []
    for r in rows:
        ex = to_prompt_completion(build_messages(schema, r["question"], r["reference_sql"]))
        out.append(
            {
                **ex,
                "id": r["id"],
                "group": r.get("source_id", r["id"]),  # a rewrite stays with its original
                "lang": r.get("lang", ""),
                "qtype": r.get("qtype", ""),
                "reference_sql": r["reference_sql"],
                "source": OWN_CONFIG,
            }
        )
    return out


def push_own() -> None:
    from datasets import Dataset

    s = get_settings()
    stage, files = own_pairs_source()
    rows = [r for f in files for r in read_jsonl(f)]
    examples = own_examples(rows, s.paths.schema.read_text("utf-8"))
    fingerprint = data_fingerprint(rows)
    if stage != "decontaminated":
        log.warning(
            "format.not_decontaminated", stage=stage,
            note="fine for a first SFT run; re-push after decontaminate.py before the final one",
        )  # fmt: skip
    repo = dataset_repo()
    message = (
        f"own: {len(examples)} pairs ({stage}), data {fingerprint}, "
        f"prompt {PROMPT_VERSION}, code {git_commit()}"
    )
    Dataset.from_list(examples).push_to_hub(
        repo, config_name=OWN_CONFIG, private=True, token=s.hf_token, commit_message=message
    )
    log.info("format.own_pushed", repo=repo, examples=len(examples), stage=stage, data=fingerprint)


def load_sft_data(
    public_max: int, own_repeat: int, eval_fraction: float, seed: int
):  # -> tuple[Dataset, Dataset]
    """Training mix for SFT: up to `public_max` Spider/Ar-Spider examples plus the own
    pairs repeated `own_repeat` times (they are fewer, and they are what matters).
    The eval set is a held-out slice of own pairs, split by group so a rewrite and its
    original never land on different sides."""
    import random

    from datasets import concatenate_datasets, get_dataset_config_names, load_dataset

    s = get_settings()
    repo = dataset_repo()
    found = get_dataset_config_names(repo, token=s.hf_token)
    missing = [c for c in (PUBLIC_CONFIG, OWN_CONFIG) if c not in found]
    if missing:
        raise RuntimeError(
            f"{repo} has no {missing} data on the Hub (found {found}). For '{OWN_CONFIG}', run"
            " `python -m sqlibrarian.training.format push-own` on the laptop first;"
            f" for '{PUBLIC_CONFIG}', run the training-data notebook."
        )
    public = load_dataset(repo, PUBLIC_CONFIG, split="train", token=s.hf_token)
    public = public.shuffle(seed=seed).select(range(min(public_max, len(public))))
    public = public.map(
        lambda ex: to_prompt_completion(ex["messages"]),
        remove_columns=[c for c in public.column_names if c not in ("source",)],
    )

    own = load_dataset(repo, OWN_CONFIG, split="train", token=s.hf_token)
    groups = sorted(set(own["group"]))
    random.Random(seed).shuffle(groups)
    eval_groups = set(groups[: max(1, int(len(groups) * eval_fraction))])
    own_eval = own.filter(lambda ex: ex["group"] in eval_groups)
    own_train = own.filter(lambda ex: ex["group"] not in eval_groups)

    keep = ["prompt", "completion", "source"]
    own_train = own_train.remove_columns([c for c in own_train.column_names if c not in keep])
    own_eval = own_eval.remove_columns([c for c in own_eval.column_names if c not in keep])
    train = concatenate_datasets([public] + [own_train] * own_repeat).shuffle(seed=seed)
    log.info(
        "format.sft_mix", public=len(public), own_train=len(own_train), own_repeat=own_repeat,
        train=len(train), eval=len(own_eval),
    )  # fmt: skip
    return train, own_eval


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="Format and publish training data.")
    p.add_argument("command", choices=["push-own"])
    p.parse_args()
    push_own()


if __name__ == "__main__":
    main()
