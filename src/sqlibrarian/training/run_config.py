"""Training runs are described by YAML files in configs/, checked when loaded.

A typo or a value out of range stops the run before any GPU time is spent
(extra="forbid" rejects unknown keys instead of silently ignoring them).
"""

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sqlibrarian.helpers.config import get_settings

RunConfig = TypeVar("RunConfig", bound=BaseModel)


class LoraSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    r: int = Field(16, gt=0)
    alpha: int = Field(32, gt=0)
    dropout: float = Field(0.05, ge=0, lt=1)
    target_modules: list[str] = [
        "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj",
    ]  # fmt: skip


class SFTRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")  # also names the Hub repos
    base_model: str = Field(default_factory=lambda: get_settings().base_model)
    use_unsloth: bool = False  # faster on Kaggle when its pinned versions install cleanly
    lora: LoraSettings = LoraSettings()

    # data mix (training/format.py load_sft_data)
    public_max: int = Field(6000, ge=0)
    own_repeat: int = Field(2, ge=1)
    eval_fraction: float = Field(0.05, gt=0, lt=0.5)

    # optimisation (T4: float16, no bfloat16)
    learning_rate: float = Field(1e-4, gt=0)
    num_train_epochs: float = Field(1.0, gt=0)
    per_device_train_batch_size: int = Field(4, gt=0)
    gradient_accumulation_steps: int = Field(4, gt=0)
    warmup_ratio: float = Field(0.03, ge=0, lt=1)
    max_length: int = Field(default_factory=lambda: get_settings().max_train_tokens)
    eval_steps: int = Field(100, gt=0)
    seed: int = 42

    push_to_hub: bool = True

    @property
    def output_dir(self) -> Path:
        return get_settings().paths.runs / self.run_name


class GRPORunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    start_model: str  # the merged SFT model: a Hub repo id or a local folder
    lora: LoraSettings = LoraSettings()

    prompts_max: int = Field(1500, gt=0)  # own pairs used as GRPO prompts
    num_generations: int = Field(4, ge=2)  # completions sampled per prompt
    max_completion_length: int = Field(256, gt=0)
    temperature: float = Field(0.9, gt=0)
    beta: float = Field(0.0, ge=0)  # KL penalty; 0 = no reference model (saves memory)
    learning_rate: float = Field(5e-6, gt=0)
    num_train_epochs: float = Field(1.0, gt=0)
    per_device_train_batch_size: int = Field(4, gt=0)
    gradient_accumulation_steps: int = Field(2, gt=0)
    reward_students: int = Field(3, ge=1)  # sample students each completion is run as
    seed: int = 42
    push_to_hub: bool = True

    @model_validator(mode="after")
    def batch_divisible_by_generations(self) -> "GRPORunConfig":
        batch = self.per_device_train_batch_size * self.gradient_accumulation_steps
        if batch % self.num_generations:
            raise ValueError(
                f"batch size x accumulation ({batch}) must be divisible by "
                f"num_generations ({self.num_generations}) (TRL GRPO requirement)"
            )
        return self

    @property
    def output_dir(self) -> Path:
        return get_settings().paths.runs / self.run_name


def load_run_config(path: Path, model: type[RunConfig]) -> RunConfig:
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    return model.model_validate(data)
