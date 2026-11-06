"""Training pieces that run without a GPU: example format and run configs."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from sqlibrarian.helpers.prompt import build_messages
from sqlibrarian.training.format import own_examples, to_prompt_completion
from sqlibrarian.training.run_config import GRPORunConfig, SFTRunConfig, load_run_config

ROOT = Path(__file__).resolve().parents[1]


def test_prompt_completion_puts_only_the_sql_in_the_completion():
    ex = to_prompt_completion(build_messages("TABLE t (a INT)", "my gpa?", "SELECT 1"))
    assert [m["role"] for m in ex["prompt"]] == ["system", "user"]
    assert ex["completion"] == [{"role": "assistant", "content": "SELECT 1"}]


def test_prompt_completion_needs_an_answer():
    with pytest.raises(ValueError):
        to_prompt_completion(build_messages("TABLE t (a INT)", "my gpa?"))


def test_rewrites_are_grouped_with_their_original():
    rows = [
        {"id": "own-1", "question": "q", "reference_sql": "SELECT 1", "lang": "en"},
        {"id": "own-1-eg", "source_id": "own-1", "question": "س", "reference_sql": "SELECT 1"},
    ]
    assert {e["group"] for e in own_examples(rows, "TABLE t (a INT)")} == {"own-1"}


def test_every_shipped_config_is_valid():
    for path in sorted((ROOT / "configs").glob("sft_*.yaml")):
        load_run_config(path, SFTRunConfig)
    for path in sorted((ROOT / "configs").glob("grpo_*.yaml")):
        load_run_config(path, GRPORunConfig)


def test_config_rejects_typos_and_bad_names():
    with pytest.raises(ValidationError):
        SFTRunConfig(run_name="ok", learnin_rate=1e-4)
    with pytest.raises(ValidationError):
        SFTRunConfig(run_name="Has Spaces")
