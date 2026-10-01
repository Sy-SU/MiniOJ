from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class SubmissionStatus(StrEnum):
    QUEUED = "QUEUED"
    COMPILING = "COMPILING"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"


class Verdict(StrEnum):
    AC = "AC"
    WA = "WA"
    CE = "CE"
    RE = "RE"
    TLE = "TLE"
    MLE = "MLE"
    OLE = "OLE"
    IE = "IE"


class TestcaseBuildStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class CustomRunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessResultLike(Protocol):
    exit_code: int
    stdout: str
    stderr: str
    time_ms: int
    memory_kb: int | None
    timed_out: bool
    output_exceeded: bool
    oom_killed: bool
    stdout_truncated: bool
    stderr_truncated: bool


def compile_result_payload(result: ProcessResultLike) -> dict[str, object]:
    success = not (
        result.exit_code != 0
        or result.timed_out
        or result.output_exceeded
        or result.oom_killed
    )
    return {
        "success": success,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "time_ms": result.time_ms,
        "memory_kb": result.memory_kb,
        "timed_out": result.timed_out,
        "output_exceeded": result.output_exceeded,
        "oom_killed": result.oom_killed,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
        "output_truncated": result.stdout_truncated or result.stderr_truncated,
    }


def testcase_result_payload(
    index: int, verdict: Verdict, result: ProcessResultLike
) -> dict[str, object]:
    return {
        "test_index": index,
        "verdict": verdict.value,
        "exit_code": result.exit_code,
        "time_ms": result.time_ms,
        "memory_kb": result.memory_kb,
        "timed_out": result.timed_out,
        "output_exceeded": result.output_exceeded,
        "oom_killed": result.oom_killed,
        "stdout_truncated": result.stdout_truncated,
        "stderr_truncated": result.stderr_truncated,
    }


def validate_submission_result(
    status: SubmissionStatus | str,
    verdict: Verdict | str | None,
    compile_result: dict | None,
    judge_result: dict | None,
) -> None:
    parsed_status = SubmissionStatus(status)
    if parsed_status is not SubmissionStatus.FINISHED:
        if (
            verdict is not None
            or compile_result is not None
            or judge_result is not None
        ):
            raise ValueError("Non-terminal submissions cannot contain final results.")
        return
    if verdict is None:
        raise ValueError("Finished submissions require a verdict.")
    parsed_verdict = Verdict(verdict)
    if judge_result is None or judge_result.get("verdict") != parsed_verdict.value:
        raise ValueError("Finished submissions require a matching judge result.")
    if parsed_verdict is not Verdict.IE and compile_result is None:
        raise ValueError("Judged submissions require a compile result.")
