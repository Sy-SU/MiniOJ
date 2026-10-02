from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from minioj.security import EMAIL_MAX_LENGTH, PASSWORD_MAX_BYTES, USERNAME_MAX_LENGTH

FeedbackMode = Literal["full", "diagnostic", "verdict_only"]
SubmissionState = Literal["QUEUED", "COMPILING", "RUNNING", "FINISHED"]
JudgeVerdict = Literal["AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "IE"]


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

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class SubmissionCreate(BaseModel):
    problem_id: str = Field(min_length=3, max_length=80)
    language: Literal["cpp20"] = "cpp20"
    source_code: str


class RunRequest(BaseModel):
    source_code: str | None = None
    code: str | None = None
    language: Literal["cpp20"] = "cpp20"
    stdin: str = ""

    @model_validator(mode="after")
    def resolve_source_code(self) -> RunRequest:
        if self.source_code is None and self.code is None:
            raise ValueError("source_code is required")
        if (
            self.source_code is not None
            and self.code is not None
            and self.source_code != self.code
        ):
            raise ValueError("source_code and code must match when both are provided")
        if self.source_code is None:
            self.source_code = self.code
        return self


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
    checker: Literal["lines", "tokens", "yesno", "testlib"] = "lines"
    checker_source: str | None = Field(default=None, min_length=20, max_length=262144)

    @model_validator(mode="after")
    def special_checker_source(self):
        if (self.checker == "testlib") != (self.checker_source is not None):
            raise ValueError(
                "testlib requires checker_source; builtin checkers forbid it"
            )
        return self


class TestCaseCreate(BaseModel):
    type: str = "hidden"
    input: str = ""
    output: str = ""
    input_sha256: str | None = None
    output_sha256: str | None = None


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContestStandingProblemResponse(BaseModel):
    problem_id: str
    label: str


class ContestStandingCellResponse(BaseModel):
    solved: bool
    wrong: int
    minute: int | None
    pending: bool


class ContestStandingRowResponse(BaseModel):
    rank: int
    user_id: int
    username: str
    solved: int
    penalty: int
    performance: int = Field(ge=0, le=4000)
    problems: dict[str, ContestStandingCellResponse]


class ContestStandingsResponse(BaseModel):
    contest_id: int
    problems: list[ContestStandingProblemResponse]
    rows: list[ContestStandingRowResponse]


class ValidationIssue(BaseModel):
    location: list[str | int]
    message: str
    type: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    details: list[ValidationIssue] | dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """Stable API error envelope plus the pre-Phase-4 compatibility field."""

    error: ErrorInfo
    detail: str | dict[str, Any] | list[ValidationIssue]


class UserCreatedResponse(BaseModel):
    id: int
    username: str
    email: str
    role: Literal["user", "admin", "system"]


class CurrentUserResponse(UserCreatedResponse):
    is_active: bool
    created_at: datetime
    feedback_mode: FeedbackMode


class TokenMetadataResponse(BaseModel):
    id: str
    name: str
    token_preview: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None


class TokenCreatedResponse(BaseModel):
    id: str
    name: str
    token: str
    token_preview: str
    expires_at: datetime


class ProblemLimitsResponse(BaseModel):
    time_ms: int
    memory_mb: int


class ProblemSampleResponse(BaseModel):
    input: str
    output: str


class ProblemSummaryResponse(BaseModel):
    problem_id: str
    title: str
    source: str | None
    source_id: str | None
    rating: int | None
    tags: list[str]
    limits: ProblemLimitsResponse


class AgentProblemResponse(BaseModel):
    problem_id: str
    title: str
    statement: str
    input_specification: str
    output_specification: str
    notes: str
    limits: ProblemLimitsResponse
    samples: list[ProblemSampleResponse]


class ProblemDetailResponse(AgentProblemResponse):
    source: str | None
    source_id: str | None
    source_url: str | None
    rating: int | None
    tags: list[str]
    checker: str


class SubmissionCreatedResponse(BaseModel):
    submission_id: int
    status: Literal["QUEUED"]


class SubmissionTestsResponse(BaseModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed_test: int | None = Field(default=None, ge=1)


class SubmissionResourcesResponse(BaseModel):
    time_ms: int = Field(ge=0)
    memory_kb: int | None = Field(default=None, ge=0)


class SubmissionDetailResponse(BaseModel):
    submission_id: int
    problem_id: str
    language: Literal["cpp20"]
    status: SubmissionState
    verdict: JudgeVerdict | None
    tests: SubmissionTestsResponse | None
    resources: SubmissionResourcesResponse | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class FeedbackCompileResponse(BaseModel):
    success: bool | None
    exit_code: int | None
    time_ms: int | None = Field(ge=0)
    memory_kb: int | None = Field(ge=0)
    timed_out: bool
    output_exceeded: bool
    oom_killed: bool
    output_truncated: bool
    stdout: str | None = None
    stderr: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class FeedbackDiagnosticResponse(BaseModel):
    message: str
    message_truncated: bool


class FeedbackFailureResponse(BaseModel):
    test_index: int = Field(ge=1)
    is_sample: bool | None = None
    input: str | None = None
    expected: str | None = None
    actual: str | None = None
    stderr: str | None = None
    input_truncated: bool = False
    expected_truncated: bool = False
    actual_truncated: bool = False
    stderr_truncated: bool = False


class FeedbackTestResultResponse(BaseModel):
    test_index: int = Field(ge=1)
    verdict: JudgeVerdict | None
    time_ms: int | None = Field(ge=0)
    memory_kb: int | None = Field(ge=0)
    exit_code: int | None = None
    timed_out: bool = False
    output_exceeded: bool = False
    oom_killed: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class FeedbackResponse(BaseModel):
    """Required machine fields; optional legacy views retain their existing names."""

    submission_id: int
    status: SubmissionState
    verdict: JudgeVerdict | None
    feedback_mode: FeedbackMode
    failed_test: int | None = Field(ge=1)
    compile: FeedbackCompileResponse | None
    execution: SubmissionResourcesResponse | None
    diagnostic: FeedbackDiagnosticResponse | None
    summary: str
    summary_truncated: bool
    tests: SubmissionTestsResponse | None = None
    resources: SubmissionResourcesResponse | None = None
    failure: FeedbackFailureResponse | None = None
    limits: ProblemLimitsResponse | None = None
    test_results: list[FeedbackTestResultResponse] | None = None
    testcase_types: list[Literal["sample", "hidden", "generated", "unknown"]] | None = (
        None
    )
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class CustomRunResponse(BaseModel):
    status: Literal["OK", "CE", "RE", "TLE", "MLE", "OLE"]
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int = Field(ge=0)
    memory_kb: int | None = Field(default=None, ge=0)
    stdout_truncated: bool
    stderr_truncated: bool


class TestCaseResponse(BaseModel):
    id: int
    type: Literal["sample", "hidden", "generated"]
    order: int
    input_sha256: str | None
    output_sha256: str | None
    created_at: datetime
