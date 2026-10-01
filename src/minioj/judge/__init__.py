"""Docker-backed judging primitives."""

from minioj.judge.contracts import (
    CustomRunStatus,
    SubmissionStatus,
    TestcaseBuildStatus,
    Verdict,
    validate_submission_result,
)
from minioj.judge.runner import DockerJudge, InfrastructureError, TestcaseBuildError

__all__ = [
    "CustomRunStatus",
    "DockerJudge",
    "InfrastructureError",
    "SubmissionStatus",
    "TestcaseBuildError",
    "TestcaseBuildStatus",
    "Verdict",
    "validate_submission_result",
]
