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

### Databases

Two PostgreSQL 16 databases run in Docker: `records_db` (the simulated student records, port 5433)
and `app_db` (SQLibrarian's own data, port 5434).

```bash
cp .env.example .env          # then set real passwords
docker compose --env-file .env -f infra/docker-compose.yml up -d
docker compose --env-file .env -f infra/docker-compose.yml ps     # both should be "healthy"
```

The `.sql` files in `db/records/` and `db/app/` run only when a volume is first created.
After changing one, rebuild with `down -v` (this deletes the data), then `up -d`.
