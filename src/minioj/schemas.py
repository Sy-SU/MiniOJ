from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from minioj.security import EMAIL_MAX_LENGTH, PASSWORD_MAX_BYTES, USERNAME_MAX_LENGTH


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=USERNAME_MAX_LENGTH)
    email: str = Field(max_length=EMAIL_MAX_LENGTH)
    password: str = Field(min_length=10, max_length=PASSWORD_MAX_BYTES)
    password_confirmation: str = Field(min_length=10, max_length=PASSWORD_MAX_BYTES)

    @field_validator("username", "email", mode="before")
    @classmethod
    def strip_identity(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class TokenCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=90, ge=1, le=3650)


class SubmissionCreate(BaseModel):
    problem_id: str
    language: str = "cpp20"
    source_code: str


class RunRequest(BaseModel):
    code: str
    language: str = "cpp20"
    stdin: str = ""


class ProblemCreate(BaseModel):
    id: str
    title: str
    statement: str
    input_specification: str = ""
    output_specification: str = ""
    notes: str = ""
    time_limit_ms: int = Field(default=2000, ge=100, le=30000)
    memory_limit_mb: int = Field(default=256, ge=16, le=2048)
    source: str | None = None
    source_id: str | None = None
    source_url: str | None = None
    rating: int | None = None
    tags: str = ""


class TestCaseCreate(BaseModel):
    type: str = "hidden"
    input: str = ""
    output: str = ""


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
