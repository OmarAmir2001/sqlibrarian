"""Make a model answer every question with SQL, and save what it wrote.

Needs no database: scoring is a separate step (score.py) on a machine that has
records_db. Runs on a GPU (Kaggle) or, slowly, on a CPU.

    python -m sqlibrarian.evaluation.generate                   # settings.base_model
    python -m sqlibrarian.evaluation.generate --model Qwen/Qwen2.5-Coder-3B-Instruct --few-shot

Writes, for a run named <model>[-fewshot]:
    <out-dir>/<run>.jsonl       one line per question: id, raw reply, cleaned SQL, seconds
    <out-dir>/<run>.meta.json   model, prompt version, device, peak GPU memory, timings
"""

import argparse
import json
import platform
import time
from pathlib import Path

import torch
import transformers
from packaging.version import Version
from transformers import AutoModelForCausalLM, AutoTokenizer

from sqlibrarian.config import get_settings
from sqlibrarian.log import configure_logging, get_logger
from sqlibrarian.prompt import FEW_SHOT, PROMPT_VERSION, build_messages, extract_sql

log = get_logger(__name__)


def run_name(model: str, few_shot: bool) -> str:
    return model.split("/")[-1] + ("-fewshot" if few_shot else "")


def main() -> None:
    configure_logging()
    s = get_settings()
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=s.base_model)
    p.add_argument("--questions", type=Path, default=s.paths.dev_questions)
    p.add_argument("--schema", type=Path, default=s.paths.schema)
    p.add_argument("--out-dir", type=Path, default=s.paths.predictions)
    p.add_argument("--few-shot", action="store_true", help="add the 3 FEW_SHOT examples")
    p.add_argument("--max-new-tokens", type=int, default=256)
    p.add_argument("--limit", type=int, help="only the first N questions (quick test)")
    args = p.parse_args()

    questions = [json.loads(x) for x in args.questions.read_text("utf-8").splitlines() if x.strip()]
    questions = questions[: args.limit] if args.limit else questions
    schema = args.schema.read_text("utf-8")
    examples = FEW_SHOT if args.few_shot else None

    cuda = torch.cuda.is_available()
    device = "cuda" if cuda else "cpu"
    # T4 GPUs have no bfloat16, so float16 on GPU; bfloat16 on CPU (half the memory of float32).
    dtype = torch.float16 if cuda else torch.bfloat16
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    load_start = time.time()
    # transformers >= 4.56 calls the precision argument `dtype`; older versions `torch_dtype`.
    dtype_arg = "dtype" if Version(transformers.__version__) >= Version("4.56") else "torch_dtype"
    load_kwargs = {"device_map": device, dtype_arg: dtype}
    try:
        model = AutoModelForCausalLM.from_pretrained(args.model, **load_kwargs)
    except (ValueError, KeyError) as e:
        # Some newer models (e.g. Qwen3.5) are image+text models that the text-only
        # loader refuses; their image+text class still answers text-only prompts.
        log.warning("model.text_loader_failed", model=args.model, error=str(e)[:200])
        from transformers import AutoModelForImageTextToText

        model = AutoModelForImageTextToText.from_pretrained(args.model, **load_kwargs)
    model.eval()
    load_seconds = time.time() - load_start
    if cuda:
        torch.cuda.reset_peak_memory_stats()

    name = run_name(args.model, args.few_shot)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.out_dir / f"{name}.jsonl"
    seconds = []
    with out_path.open("w", encoding="utf-8") as f:
        for i, q in enumerate(questions, 1):
            messages = build_messages(schema, q["question"], examples=examples)
            # enable_thinking=False turns off Qwen3-style thinking; other templates ignore it.
            text = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
            )
            inputs = tokenizer(text, return_tensors="pt").to(device)
            t0 = time.time()
            with torch.no_grad():
                out = model.generate(
                    **inputs,
                    max_new_tokens=args.max_new_tokens,
                    do_sample=False,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )
            took = time.time() - t0
            seconds.append(took)
            reply = tokenizer.decode(
                out[0, inputs["input_ids"].shape[1] :], skip_special_tokens=True
            )
            row = {
                "id": q["id"],
                "raw": reply,
                "sql": extract_sql(reply),
                "seconds": round(took, 3),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.info(
                "generate.answer", n=i, of=len(questions), id=q["id"],
                seconds=round(took, 2), sql=row["sql"][:80],
            )  # fmt: skip

    ordered = sorted(seconds)
    meta = {
        "run": name,
        "model": args.model,
        "few_shot": args.few_shot,
        "prompt_version": PROMPT_VERSION,
        "questions_file": str(args.questions),
        "n_questions": len(questions),
        "device": torch.cuda.get_device_name(0) if cuda else platform.processor() or "cpu",
        "dtype": str(dtype).replace("torch.", ""),
        "load_seconds": round(load_seconds, 1),
        "mean_seconds": round(sum(seconds) / len(seconds), 3),
        "p95_seconds": round(ordered[int(0.95 * (len(ordered) - 1))], 3),
        "peak_gpu_memory_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2) if cuda else None,
    }
    (args.out_dir / f"{name}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    log.info("generate.done", **meta)


if __name__ == "__main__":
    main()
