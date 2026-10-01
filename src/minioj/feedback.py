from __future__ import annotations

import copy
import json

from minioj.judge import SubmissionStatus, Verdict
from minioj.models import Submission


def submission_feedback(submission: Submission, policy: str) -> dict[str, object]:
    """Return the same policy-filtered result for Web and Agent consumers."""

    if submission.status != SubmissionStatus.FINISHED.value:
        return {
            "status": submission.status,
            "verdict": None,
            "summary": "Judging is still in progress.",
        }

    stored = (
        json.loads(submission.judge_result)
        if submission.judge_result
        else {
            "verdict": Verdict.IE.value,
            "summary": "Judge result is unavailable.",
        }
    )
    result: dict[str, object] = copy.deepcopy(stored)
    compile_result = (
        json.loads(submission.compile_result) if submission.compile_result else None
    )
    if submission.verdict == Verdict.CE.value or (
        isinstance(compile_result, dict) and compile_result.get("success") is False
    ):
        # The second branch preserves display of malformed legacy rows whose
        # verdict and stored compile result disagree, without bypassing policy.
        result["compile"] = compile_result

    if policy == "verdict_only":
        return {
            "verdict": result.get("verdict"),
            "summary": result.get("summary"),
        }
    if policy == "diagnostic":
        if isinstance(result.get("failure"), dict):
            result["failure"] = {"test_index": result["failure"].get("test_index")}
        if isinstance(result.get("compile"), dict):
            compile_result = result["compile"]
            result["compile"] = {
                key: compile_result.get(key)
                for key in (
                    "success",
                    "exit_code",
                    "time_ms",
                    "memory_kb",
                    "timed_out",
                    "output_exceeded",
                    "oom_killed",
                    "output_truncated",
                )
            }
    return result
