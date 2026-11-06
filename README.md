# SQLibrarian

Ask about your student record in Arabic, English or a mix, for example *"أنا جبت كام في Data Structures؟"* or *"What's my GPA?"*, and get an answer along with the SQL that produced it.

SQLibrarian is a text-to-SQL assistant for a credit-hour CS/IS institute. A small, fine-tuned, self-hosted model (Qwen2.5-Coder-1.5B, trained with LoRA SFT and then GRPO) turns each question into one PostgreSQL `SELECT`. Before it runs, the query passes a SQL guard. It then runs against the records database, where **PostgreSQL row-level security** makes sure each student sees only their own rows. The model never sees student data.

> All data in this project is fake, generated with Faker (`ar_EG`). No real student records are used.

## Use cases

1. **Track grades** (student)
2. **Ask about available subjects**, with prerequisite checks (student)
3. **Review advising problems** flagged by a nightly rule checker (TA)
4. **Approve or reject a study plan** (TA)

## Status

Work in progress: Phase 0, setup and baseline.

## Development

```bash
uv sync                      # install dependencies (Python 3.12)
uv run pre-commit install    # run Ruff on every commit
uv run ruff check . && uv run pytest
```
