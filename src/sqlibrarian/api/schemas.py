"""Request and response shapes: the API contract, validated at the boundary."""

from pydantic import BaseModel, Field, field_validator

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import AskOutcome, Role


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role
    expires_in_minutes: int


class AskRequest(BaseModel):
    question: str = Field(min_length=1)

    @field_validator("question")
    @classmethod
    def not_too_long(cls, value: str) -> str:
        value = value.strip()
        limit = get_settings().question_max_chars
        if not value:
            raise ValueError("the question is empty")
        if len(value) > limit:
            raise ValueError(f"the question is longer than {limit} characters")
        return value


class AskResponse(BaseModel):
    answer: str  # the bilingual template sentence
    sql: str | None  # the query that ran (shown to the user), None if nothing ran
    columns: list[str] = []
    rows: list[list] = []
    outcome: AskOutcome
    timings_ms: dict[str, int] = {}
