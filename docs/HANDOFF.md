# Handoff: where SQLibrarian stands and what to do next

Written 2026-11-06, at the end of a long build session. Everything here is free to run
(Kaggle GPU quota, Hugging Face, DagsHub, Groq free tier). The private tracker is
`notes/evolution.md`; this file is the public, step-by-step version.

## 1. Where things stand

| Phase | Code | Verified | Still to do |
|---|---|---|---|
| **P0** setup + baseline | done, merged (PR #1) | baseline run on Kaggle: 4% on the dev set | — |
| **P1** data | tools done (PR #2) | 418 valid own pairs from day 1; test-set checker tested | **your 300 test questions + 60 attacks**; ~7 more Groq days; Arabic rewrites; your 200-pair review; decontamination |
| **P3** SFT | done (PR #3) | config validation, data format | **first run on Kaggle** (never run: no GPU here) |
| **P4** GRPO | done (PR #4) | the reward, against the live database | **first run on Kaggle** (after SFT) |
| **P2** safe pipeline | done (PR #5) | RLS by hand; guard: 0 of 468 good queries refused, 23 of 23 attacks refused; API end to end on both databases | try it with a real model server (step 6 below) |
| **P6** TA features | done except the UI tab (PR #6) | checker flags exactly the 44 broken plans, 0 false alarms; review permissions | Gradio TA tab |
| **P5** serving | not started | — | vLLM FP16 vs 4-bit vs GGUF, `bench_model.py`, Locust |
| **P7** release | CI done (PR #7) | CI runs all tests on real PostgreSQL | Prometheus/Grafana, MLflow traces, model card, demo Space, README video |

## 2. Merge the pull requests, in order

The PRs are **stacked**: each one is built on the one before, so each shows only its own phase.

1. Merge **#2** (`p1-data`) into `master`.
2. Open **#3** on GitHub, click **Edit** next to the title, and change the base branch to
   `master`. The diff now shows only P3. Merge it.
3. Do the same for **#4**, **#5**, **#6** and **#7**, in that order.

CI (GitHub Actions) runs on every PR: lint plus all tests against real PostgreSQL. A red check
means something broke; open the run to see which step.

After merging, change `BRANCH = ...` in the notebooks' settings cell to `"master"`.

## 3. Your next steps, in order

### 3.1 One-time setup on the laptop

1. Add a Hugging Face **write** token to `.env`: `HF_TOKEN=hf_...`.
2. Start the databases and make sure the data and logins exist:
   ```bash
   docker compose --env-file .env -f infra/docker-compose.yml up -d
   uv run python db/seed.py && uv run python db/seed_users.py
   ```
3. Publish what Kaggle needs:
   ```bash
   uv run --with datasets python -m sqlibrarian.training.format push-own   # your pairs
   uv run --with datasets python db/publish_dump.py publish                # the fake DB, for GRPO
   ```
   `--with datasets` adds only the small upload library. Don't use `--extra training` here:
   it also installs PyTorch with several GB of NVIDIA packages, which uploading doesn't need.

### 3.2 Every day until you have about 3,000 own pairs (free, about 30 minutes, unattended)

```bash
uv run python -m sqlibrarian.training.generate_pairs
uv run python -m sqlibrarian.training.validate_pairs
```

The generator stops by itself at the free daily limit. Around day 4, also run
`python -m sqlibrarian.training.rewrite_arabic` (Egyptian and mixed rewrites), then
`review_sample make` → review 200 rows in Excel → `review_sample apply`. Re-run `push-own`
afterwards.

### 3.3 First SFT run (Kaggle, about 2–3 hours)

1. Upload `notebooks/sqlibrarian-sft.ipynb` to Kaggle. Settings: GPU T4, Internet on.
2. Secrets: `HF_TOKEN`, `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD`.
3. **Save Version → Save & Run All (Commit)**, so it keeps running when you close the tab.
4. When it finishes: download `sft_predictions.zip`, unzip into `data/eval/`, and score:
   ```bash
   uv run python -m sqlibrarian.evaluation.score data/eval/predictions/*.jsonl --mlflow
   ```
5. **Gate P3:** SFT beats the 4% baseline. Record the numbers in the tracker.

This code has never run on a GPU. If a cell fails, read the last lines of the error. The usual
causes are a library version on Kaggle (fix the install line) or a secret that isn't ticked for
the notebook. Most fixes are one line.

### 3.4 GRPO (Kaggle, about 3–5 hours)

1. Put the SFT model's Hub id in `configs/grpo_run1.yaml` (`start_model:`), commit and push.
2. Run `notebooks/sqlibrarian-grpo.ipynb` the same way. It starts PostgreSQL inside Kaggle.
3. Score as above. **Gate P4:** keep whichever of SFT and GRPO scores better, and record why.

### 3.5 The test set and the attacks (by hand: the part only you can do)

- Follow `docs/test_set_guide.md`: write in `data/eval/test_questions.csv`, run
  `python -m sqlibrarian.evaluation.test_set check` as you go, then `build`.
- Then run `decontaminate.py` (removes training pairs too close to test questions), `push-own`
  again, and retrain once for the final numbers. Tag it: `git tag testset-v1`.
- The 60 attacks (`data/eval/attacks.jsonl`): questions that try to read another student, list
  everyone, change data, or inject instructions in Arabic and English. The target is
  **0 foreign rows**. `tests/test_api.py` shows how one attack is checked through the API.

### 3.6 Try the assistant yourself

```bash
llama-server -hf <your-hf-user>/sqlibrarian-sft-run1-gguf --port 8080     # after SFT
uv run uvicorn sqlibrarian.api.main:app --port 8000
uv run --extra ui python -m sqlibrarian.ui.app                             # http://localhost:7860
```

Log in with a student id (for example `238834`) or `ta1`, and the `DEMO_PASSWORD` from `.env`.
Until the SFT model exists, the base model (`Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF:Q4_K_M`)
works but answers badly (4%).

The laptop has 7.7 GB of RAM: a 1.5B Q4 model (about 1.2 GB) runs fine; never run *training*
locally (it gets killed and takes the database containers down; restart them with `up -d`).

## 4. Code still to write

| Piece | Where | Notes |
|---|---|---|
| Gradio TA tab | `ui/app.py` | calls `GET /plans/pending` and `POST /plans/{id}/review`, both already done |
| Eval through the API | `evaluation/run_api.py` | the attack set and the test set through `/ask` as several students; count foreign rows |
| Serving benchmark | `evaluation/bench_model.py`, `tests/load/locustfile.py` | vLLM FP16 vs AWQ/GPTQ 4-bit vs GGUF CPU on the same questions; p95 latency target 5 s |
| Monitoring | `api/metrics.py`, `infra/prometheus.yml`, `infra/grafana/` | request count/latency by outcome, guard refusals, retries |
| Release | Hub model card, CPU demo Space, README video | |

Write new code the same way as the rest: settings in `helpers/config.py`, fixed values in
`helpers/enums.py`, `structlog` via `helpers/log.py`, prompts only in `helpers/prompt.py`, tests next to it.

## 5. Decisions from today to review

- **SFT/GRPO backend:** plain TRL + PEFT by default; Unsloth (the plan's choice) is
  `use_unsloth: true` in a config. Reason: Unsloth pins its own versions and often breaks on Kaggle;
  try it once the plain run works.
- **Scoring is strict:** a prediction is correct only if it returns the same rows *and columns* as
  the reference, for 5 different students. It understates every model equally.
- **The dev questions were written by Claude** at your request; rewrite any that don't sound like
  your students. The test set must be yours.
- **Seed realism:** course difficulty (`HARD`/`EASY` in `db/seed.py`) is a guess; average GPA is 2.25.

## 6. Where things are

```
src/sqlibrarian/helpers/   config (all settings), enums, log, prompt, lineage, hub
src/sqlibrarian/api/       guard, runner, generator, composer, orchestrator, identity, approvals, main
src/sqlibrarian/training/  generate/validate pairs, rewrite, review, decontaminate, format, sft, grpo, rewards, export
src/sqlibrarian/evaluation/ generate, score (judge), check_questions, test_set, db
src/sqlibrarian/plan_checker/ rules, run
src/sqlibrarian/ui/        app (student tab)
db/                        schema, catalog, security, seed, seed_users, export_schema, publish_dump
configs/                   one YAML per training run
notebooks/                 training data, baseline, SFT, GRPO (Kaggle)
docs/                      baseline report, test-set guide, this file
```
