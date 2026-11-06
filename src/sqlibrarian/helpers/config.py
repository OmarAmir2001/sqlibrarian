"""All settings, in one typed place.

Values come from environment variables or the .env file in the repo root
(names are case-insensitive: RECORDS_DB_URL fills records_db_url). Everything
except secrets has a default, so the same code runs on a laptop and on Kaggle;
override any of them in .env without touching code. .env.example lists them.

    from sqlibrarian.helpers.config import get_settings
    s = get_settings()
    s.base_model, s.paths.dev_questions, s.records_dsn
"""

from functools import cached_property, lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/sqlibrarian/helpers/config.py -> the repo root (also on Kaggle: editable install)
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Paths:
    """Every data file the scripts read or write, built from one root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.eval = self.data / "eval"
        self.training = self.data / "training"
        # evaluation
        self.schema = self.eval / "schema.txt"
        self.dev_questions = self.eval / "dev_questions.jsonl"
        self.test_questions = self.eval / "test_questions.jsonl"  # built, frozen at testset-v1
        self.test_questions_csv = self.eval / "test_questions.csv"  # written by hand
        self.attacks = self.eval / "attacks.jsonl"
        self.predictions = self.eval / "predictions"
        self.plan_flags_expected = self.eval / "plan_flags_expected.jsonl"
        # training data, in the order the pipeline makes it
        self.public = self.training / "public"
        self.public_filtered = self.training / "public_filtered"
        self.raw_generated = self.training / "raw_generated.jsonl"
        self.own_pairs = self.training / "own_pairs.jsonl"
        self.own_pairs_arabic = self.training / "own_pairs_arabic.jsonl"
        self.review_sheet = self.training / "review_sample.csv"
        self.own_pairs_reviewed = self.training / "own_pairs_reviewed.jsonl"
        self.own_pairs_clean = self.training / "own_pairs_clean.jsonl"
        self.configs = root / "configs"  # one YAML file per training run
        self.runs = root / "runs"  # training outputs (git-ignored)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_REPO_ROOT / ".env", extra="ignore")

    # --- secrets and connections (no defaults: they live only in .env) -------------
    records_db_url: str | None = None
    app_db_url: str | None = None
    groq_api_key: str | None = None
    mlflow_tracking_uri: str | None = None
    mlflow_tracking_username: str | None = None
    mlflow_tracking_password: str | None = None
    hf_token: str | None = None  # write token: datasets and models on the Hugging Face Hub

    # --- models -----------------------------------------------------------------------
    base_model: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct"  # chosen at the P0 gate
    pair_generator_model: str = "openai/gpt-oss-120b"  # writes training pairs (Groq)
    pair_generator_base_url: str = "https://api.groq.com/openai/v1"
    embedding_model: str = "intfloat/multilingual-e5-large"  # decontamination

    # --- limits -----------------------------------------------------------------------
    max_train_tokens: int = Field(2048, gt=0)  # longer training examples are dropped
    statement_timeout: str = "3s"  # every query run against records_db
    result_row_limit: int = Field(200, gt=0)  # the guard adds LIMIT 200 to every query
    groq_max_calls_per_day: int = Field(30, gt=0)  # ~6,000 tokens each, under 200K/day
    groq_wait_seconds: float = Field(45.0, ge=0)  # between calls, under 8K tokens/minute
    decontamination_threshold: float = Field(0.9, gt=0, le=1)  # cosine similarity
    mlflow_baseline_experiment: str = "sqlibrarian-baseline"
    test_set_per_lang: int = Field(100, gt=0)  # en / ar / mixed questions in the test set

    # --- Hugging Face Hub (private repos, under the token's user) --------------------
    hf_dataset_name: str = "sqlibrarian-training-data"  # configs: public, own
    hf_model_prefix: str = "sqlibrarian"  # models: <prefix>-<run name>

    # --- training ---------------------------------------------------------------------
    mlflow_sft_experiment: str = "sqlibrarian-sft"
    mlflow_grpo_experiment: str = "sqlibrarian-grpo"

    # --- logging ----------------------------------------------------------------------
    log_level: str = "INFO"
    log_format: str = "console"  # "console" on a laptop, "json" for machines

    project_root: Path = _REPO_ROOT

    @cached_property
    def paths(self) -> Paths:
        return Paths(self.project_root)

    @property
    def records_dsn(self) -> str:
        """RECORDS_DB_URL, or a clear error for scripts that need the database.
        (Optional in the class so GPU scripts on Kaggle, which have no database,
        still load the settings.)"""
        if not self.records_db_url:
            raise SystemExit("RECORDS_DB_URL is not set: add it to .env (see .env.example)")
        return self.records_db_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
