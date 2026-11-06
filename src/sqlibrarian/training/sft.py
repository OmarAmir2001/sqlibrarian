"""Step 5: LoRA supervised fine-tuning (Kaggle, one T4).

    python -m sqlibrarian.training.sft --config configs/sft_run1.yaml

What it does, in order:
  1. reads the run config (configs/*.yaml) and the training mix from the Hub
     (Spider + Ar-Spider + your own pairs, as prompt/completion examples)
  2. loads the base model in float16 and adds LoRA adapters (plain PEFT, or Unsloth
     when the config says use_unsloth: true)
  3. trains with TRL's SFTTrainer; the loss is on the SQL only, and eval loss on held-out
     own pairs is checked every eval_steps; the best checkpoint is kept
  4. saves the adapter and a merged float16 model, each WITH its tokenizer, the schema
     text and the prompt version (the model's "preprocessing" travels with it)
  5. pushes both to private Hub repos and logs everything to MLflow (DagsHub):
     params, metrics, git commit, data revision, prompt version, output repos

Secrets (Kaggle: Add-ons > Secrets): HF_TOKEN, MLFLOW_TRACKING_URI,
MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD.
"""

import argparse
import json
import os
import time
from pathlib import Path

from pydantic import BaseModel

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.hub import dataset_repo, dataset_revision, model_repo
from sqlibrarian.helpers.lineage import git_commit
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.helpers.prompt import PROMPT_VERSION
from sqlibrarian.training.format import load_sft_data
from sqlibrarian.training.run_config import SFTRunConfig, load_run_config

log = get_logger(__name__)


def mlflow_env(experiment: str) -> bool:
    """Point MLflow (and the Trainer's MLflow callback) at the tracking server."""
    s = get_settings()
    if not s.mlflow_tracking_uri:
        log.warning("mlflow.not_configured", note="training runs, but nothing is tracked")
        return False
    os.environ["MLFLOW_TRACKING_URI"] = s.mlflow_tracking_uri
    os.environ["MLFLOW_TRACKING_USERNAME"] = s.mlflow_tracking_username or ""
    os.environ["MLFLOW_TRACKING_PASSWORD"] = s.mlflow_tracking_password or ""
    os.environ["MLFLOW_EXPERIMENT_NAME"] = experiment
    return True


def flat(prefix: str, data: dict) -> dict:
    out = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out |= flat(f"{prefix}{k}.", v)
        else:
            out[f"{prefix}{k}"] = v if not isinstance(v, list) else ",".join(map(str, v))
    return out


def load_model(cfg: SFTRunConfig):
    """Returns (model, tokenizer, peft_config). With Unsloth the adapters are already
    attached (peft_config None); otherwise SFTTrainer attaches them."""
    import torch

    if cfg.use_unsloth:
        from unsloth import FastLanguageModel  # must be imported before trl

        model, tokenizer = FastLanguageModel.from_pretrained(
            cfg.base_model, max_seq_length=cfg.max_length, dtype=None, load_in_4bit=False
        )
        model = FastLanguageModel.get_peft_model(
            model, r=cfg.lora.r, lora_alpha=cfg.lora.alpha, lora_dropout=cfg.lora.dropout,
            target_modules=cfg.lora.target_modules, use_gradient_checkpointing="unsloth",
            random_state=cfg.seed,
        )  # fmt: skip
        return model, tokenizer, None

    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(cfg.base_model)
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = AutoModelForCausalLM.from_pretrained(cfg.base_model, dtype=dtype)
    peft_config = LoraConfig(
        r=cfg.lora.r, lora_alpha=cfg.lora.alpha, lora_dropout=cfg.lora.dropout,
        target_modules=cfg.lora.target_modules, task_type="CAUSAL_LM",
    )  # fmt: skip
    return model, tokenizer, peft_config


def save_bundle(folder: Path, cfg: BaseModel, lineage: dict) -> None:
    """Next to the weights: what the model must be fed (schema + prompt version) and
    what produced it."""
    s = get_settings()
    (folder / "schema.txt").write_text(s.paths.schema.read_text("utf-8"), "utf-8")
    (folder / "sqlibrarian.json").write_text(
        json.dumps({"lineage": lineage, "config": cfg.model_dump(mode="json")}, indent=2), "utf-8"
    )


def push(folder: Path, repo: str, message: str) -> str:
    from huggingface_hub import HfApi

    api = HfApi(token=get_settings().hf_token)
    api.create_repo(repo, private=True, exist_ok=True)
    info = api.upload_folder(folder_path=str(folder), repo_id=repo, commit_message=message)
    return info.oid if hasattr(info, "oid") else "unknown"


def train(cfg: SFTRunConfig) -> None:
    import torch

    model, tokenizer, peft_config = load_model(cfg)  # Unsloth (if used) before trl
    from trl import SFTConfig, SFTTrainer

    s = get_settings()
    tracking = mlflow_env(s.mlflow_sft_experiment)
    data_repo = dataset_repo()
    lineage = {
        "git_commit": git_commit(),
        "data_repo": data_repo,
        "data_revision": dataset_revision(data_repo),
        "prompt_version": PROMPT_VERSION,
        "base_model": cfg.base_model,
        "backend": "unsloth" if cfg.use_unsloth else "trl+peft",
    }
    train_ds, eval_ds = load_sft_data(cfg.public_max, cfg.own_repeat, cfg.eval_fraction, cfg.seed)
    cuda = torch.cuda.is_available()

    args = SFTConfig(
        output_dir=str(cfg.output_dir / "checkpoints"),
        run_name=cfg.run_name,
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        per_device_eval_batch_size=cfg.per_device_train_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        learning_rate=cfg.learning_rate,
        warmup_steps=cfg.warmup_ratio,  # transformers v5: a value below 1 is a ratio
        max_length=cfg.max_length,  # TRL's default is 1024; our limit is 2,048
        fp16=cuda,  # T4 has no bfloat16, and TRL turns bf16 on by default
        bf16=False,
        logging_steps=10,
        eval_strategy="steps",
        eval_steps=cfg.eval_steps,
        save_strategy="steps",
        save_steps=cfg.eval_steps,
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        report_to=["mlflow"] if tracking else "none",
        seed=cfg.seed,
    )
    trainer = SFTTrainer(
        model=model, args=args, train_dataset=train_ds, eval_dataset=eval_ds,
        processing_class=tokenizer, peft_config=peft_config,
    )  # fmt: skip

    import mlflow

    with mlflow.start_run(run_name=cfg.run_name) if tracking else _no_run():
        if tracking:
            mlflow.set_tags(lineage)
            mlflow.log_params(flat("cfg.", cfg.model_dump(mode="json")))
            mlflow.log_params({"data.train": len(train_ds), "data.eval": len(eval_ds)})
        started = time.time()
        result = trainer.train()
        metrics = {**result.metrics, **trainer.evaluate()}
        metrics["train_minutes"] = round((time.time() - started) / 60, 1)
        if cuda:
            metrics["peak_gpu_memory_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
        log.info("sft.trained", **{k: round(v, 4) for k, v in metrics.items()})

        adapter_dir = cfg.output_dir / "adapter"
        merged_dir = cfg.output_dir / "merged"
        trainer.save_model(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        if cfg.use_unsloth:
            trainer.model.save_pretrained_merged(
                str(merged_dir), tokenizer, save_method="merged_16bit"
            )
        else:
            trainer.model.merge_and_unload().save_pretrained(str(merged_dir))
            tokenizer.save_pretrained(str(merged_dir))
        for folder in (adapter_dir, merged_dir):
            save_bundle(folder, cfg, lineage)
        log.info("sft.saved", adapter=str(adapter_dir), merged=str(merged_dir))

        repos = {}
        if cfg.push_to_hub:
            message = (
                f"{cfg.run_name}: eval_loss {metrics.get('eval_loss', 0):.4f}, "
                f"code {lineage['git_commit']}, data {lineage['data_revision'][:8]}"
            )
            repos = {
                "hub_merged": model_repo(cfg.run_name),
                "hub_adapter": model_repo(f"{cfg.run_name}-lora"),
            }
            push(merged_dir, repos["hub_merged"], message)
            push(adapter_dir, repos["hub_adapter"], message)
            log.info("sft.pushed", **repos)
        if tracking:
            mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, int | float)})
            mlflow.set_tags(repos)


class _no_run:
    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    args = p.parse_args()
    cfg = load_run_config(args.config, SFTRunConfig)
    log.info("sft.start", run=cfg.run_name, base_model=cfg.base_model, unsloth=cfg.use_unsloth)
    train(cfg)


if __name__ == "__main__":
    main()
