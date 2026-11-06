"""Settings read from environment variables or the .env file in the repo root.

Every script and the API read connection details from here, so they are written once.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    records_db_url: str
    app_db_url: str | None = None
    groq_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
