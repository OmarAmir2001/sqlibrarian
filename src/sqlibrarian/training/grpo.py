"""Step 7: GRPO on top of the SFT model, rewarded by running the SQL (Kaggle, one T4).

    python -m sqlibrarian.training.grpo --config configs/grpo_run1.yaml

What it does, in order:
  1. loads the merged SFT model (config start_model: a Hub repo or a local folder) and
     adds fresh LoRA adapters
  2. builds prompts from your own pairs (HF config "own"): the same build_messages()
     prompt, plus the reference SQL the reward compares against
  3. for every prompt, samples num_generations queries; training/rewards.py runs each
     one against records_db as several students and scores it (1.0 correct ... -1.0 for
     filtering by student); GRPO moves the model toward the better ones
  4. merges, saves with tokenizer, schema and lineage, pushes to private Hub repos and
     logs to MLflow (experiment settings.mlflow_grpo_experiment), like sft.py

Needs records_db reachable at RECORDS_DB_URL: the GRPO notebook starts Postgres inside
Kaggle and restores the fake data from the dataset repo (db/publish_dump.py).
"""

import argparse
import time
from pathlib import Path

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.hub import dataset_repo, dataset_revision, model_repo
from sqlibrarian.helpers.lineage import git_commit
from sqlibrarian.helpers.log import configure_logging, get_logger
from sqlibrarian.helpers.prompt import PROMPT_VERSION
from sqlibrarian.training.format import OWN_CONFIG
from sqlibrarian.training.rewards import ExecutionReward
from sqlibrarian.training.run_config import GRPORunConfig, load_run_config
from sqlibrarian.training.sft import _no_run, flat, mlflow_env, push, save_bundle

log = get_logger(__name__)


def load_prompts(cfg: GRPORunConfig):
    """Own pairs -> {"prompt": [system, user], "reference_sql": ...}."""
    from datasets import load_dataset

    s = get_settings()
    own = load_dataset(dataset_repo(), OWN_CONFIG, split="train", token=s.hf_token)
    own = own.shuffle(seed=cfg.seed).select(range(min(cfg.prompts_max, len(own))))
    keep = {"prompt", "reference_sql"}
    return own.remove_columns([c for c in own.column_names if c not in keep])


def train(cfg: GRPORunConfig) -> None:
    import torch
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import GRPOConfig, GRPOTrainer

    s = get_settings()
    tracking = mlflow_env(s.mlflow_grpo_experiment)
    data_repo = dataset_repo()
    lineage = {
        "git_commit": git_commit(),
        "data_repo": data_repo,
        "data_revision": dataset_revision(data_repo),
        "prompt_version": PROMPT_VERSION,
        "start_model": cfg.start_model,
        "reward": "execution_match_v1",
    }
    prompts = load_prompts(cfg)
    cuda = torch.cuda.is_available()

    tokenizer = AutoTokenizer.from_pretrained(cfg.start_model)
    model = AutoModelForCausalLM.from_pretrained(
        cfg.start_model, dtype=torch.float16 if cuda else torch.float32
    )
    peft_config = LoraConfig(
        r=cfg.lora.r, lora_alpha=cfg.lora.alpha, lora_dropout=cfg.lora.dropout,
        target_modules=cfg.lora.target_modules, task_type="CAUSAL_LM",
    )  # fmt: skip
    args = GRPOConfig(
        output_dir=str(cfg.output_dir / "checkpoints"),
        run_name=cfg.run_name,
        learning_rate=cfg.learning_rate,
        num_train_epochs=cfg.num_train_epochs,
        per_device_train_batch_size=cfg.per_device_train_batch_size,
        gradient_accumulation_steps=cfg.gradient_accumulation_steps,
        num_generations=cfg.num_generations,
        max_completion_length=cfg.max_completion_length,
        temperature=cfg.temperature,
        beta=cfg.beta,
        fp16=cuda,  # T4: no bfloat16
        bf16=False,
        logging_steps=5,
        save_steps=50,
        save_total_limit=2,
        report_to=["mlflow"] if tracking else "none",
        seed=cfg.seed,
    )
    trainer = GRPOTrainer(
        model=model, reward_funcs=[ExecutionReward(cfg.reward_students)], args=args,
        train_dataset=prompts, processing_class=tokenizer, peft_config=peft_config,
    )  # fmt: skip

    import mlflow

    with mlflow.start_run(run_name=cfg.run_name) if tracking else _no_run():
        if tracking:
            mlflow.set_tags(lineage)
            mlflow.log_params(flat("cfg.", cfg.model_dump(mode="json")))
            mlflow.log_param("data.prompts", len(prompts))
        started = time.time()
        result = trainer.train()
        metrics = dict(result.metrics)
        metrics["train_minutes"] = round((time.time() - started) / 60, 1)
        if cuda:
            metrics["peak_gpu_memory_gb"] = round(torch.cuda.max_memory_allocated() / 1e9, 2)
        log.info("grpo.trained", **{k: round(v, 4) for k, v in metrics.items()})

        adapter_dir = cfg.output_dir / "adapter"
        merged_dir = cfg.output_dir / "merged"
        trainer.save_model(str(adapter_dir))
        tokenizer.save_pretrained(str(adapter_dir))
        trainer.model.merge_and_unload().save_pretrained(str(merged_dir))
        tokenizer.save_pretrained(str(merged_dir))
        for folder in (adapter_dir, merged_dir):
            save_bundle(folder, cfg, lineage)
        log.info("grpo.saved", merged=str(merged_dir))

        repos = {}
        if cfg.push_to_hub:
            message = f"{cfg.run_name}: from {cfg.start_model}, code {lineage['git_commit']}"
            repos = {
                "hub_merged": model_repo(cfg.run_name),
                "hub_adapter": model_repo(f"{cfg.run_name}-lora"),
            }
            push(merged_dir, repos["hub_merged"], message)
            push(adapter_dir, repos["hub_adapter"], message)
            log.info("grpo.pushed", **repos)
        if tracking:
            mlflow.log_metrics({k: v for k, v in metrics.items() if isinstance(v, int | float)})
            mlflow.set_tags(repos)


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, required=True)
    args = p.parse_args()
    cfg = load_run_config(args.config, GRPORunConfig)
    log.info("grpo.start", run=cfg.run_name, start_model=cfg.start_model)
    train(cfg)


if __name__ == "__main__":
    main()
