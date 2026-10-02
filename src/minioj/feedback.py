from __future__ import annotations

import json
import re

from minioj.config import settings
from minioj.judge import SubmissionStatus, Verdict
from minioj.models import Submission
from minioj.problems import _testcase_path

PREVIEW_BYTES = 1024
VERDICT_MESSAGES = {
    "AC": "Accepted.",
    "WA": "Wrong answer.",
    "CE": "Compilation failed.",
    "RE": "Runtime error.",
    "TLE": "Time limit exceeded.",
    "MLE": "Memory limit exceeded.",
    "OLE": "Output limit exceeded.",
    "IE": "The judge could not complete this submission. Please contact an administrator.",
}


def bounded_text(value: str, *, truncated=False) -> tuple[str, bool]:
    raw = value.encode("utf-8", errors="replace")
    return raw[:PREVIEW_BYTES].decode("utf-8", errors="ignore"), truncated or len(
        raw
    ) > PREVIEW_BYTES


def safe_diagnostic(value: str, *, truncated=False) -> tuple[str, bool]:
    """Compiler diagnostics are untrusted text, not a channel for infrastructure logs."""
    # Limit work before regexes; upstream flags also survive already-clipped records.
    clipped = truncated or len(value.encode("utf-8", errors="replace")) > PREVIEW_BYTES
    if settings.secret_key:
        value = value.replace(settings.secret_key, "[redacted]")
    value, clipped = bounded_text(value, truncated=clipped)
    value = re.sub(r"(?i)\b(?:oj_[a-z0-9_-]+|bearer\s+\S+)", "[redacted]", value)
    value = re.sub(
        r"(?i)\b(?:secret|token|password|api[_-]?key)\s*[:=]\s*\S+", "[redacted]", value
    )
    value = re.sub(
        r"(?i)\b[a-z]:[\\/][^\s\"'<>]+|(?<!\w)/[^\s\"'<>:]+", "[path]", value
    )
    lines = []
    for line in value.splitlines(keepends=True):
        if re.search(
            r"(?i)docker|traceback|\.env\b|sqlalchemy|sqlite|\b(?:select|insert|update|delete)\s+.*\b(?:from|into|set)\b",
            line,
        ):
            lines.append("[internal diagnostic omitted]\n")
        else:
            lines.append(line)
    return bounded_text("".join(lines), truncated=clipped)


def _object(value) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _number(value, *, minimum=0):
    return value if type(value) is int and value >= minimum else None


def compile_feedback(stored: dict, *, include_text: bool) -> dict:
    result = {
        "success": stored.get("success")
        if type(stored.get("success")) is bool
        else None,
        "exit_code": stored.get("exit_code")
        if type(stored.get("exit_code")) is int
        else None,
        "time_ms": _number(stored.get("time_ms")),
        "memory_kb": _number(stored.get("memory_kb")),
        **{
            key: stored.get(key) is True
            for key in (
                "timed_out",
                "output_exceeded",
                "oom_killed",
                "output_truncated",
            )
        },
    }
    if include_text:
        for key in ("stdout", "stderr"):
            result[key], result[key + "_truncated"] = safe_diagnostic(
                stored.get(key) if isinstance(stored.get(key), str) else "",
                truncated=stored.get(key + "_truncated") is True
                or stored.get("output_truncated") is True,
            )
        result["output_truncated"] = (
            result["output_truncated"]
            or result["stdout_truncated"]
            or result["stderr_truncated"]
        )
    return result


def submission_testcase_rows(feedback: dict[str, object]) -> list[dict[str, object]]:
    """Build Web-only rows from policy-filtered metadata, never testcase content."""
    records = feedback.get("test_results")
    if not isinstance(records, list):
        # Older submissions did not retain individual resource measurements.
        return []

    def measurement(value: object) -> int | None:
        return value if type(value) is int and value >= 0 else None

    by_index = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        index = measurement(record.get("test_index"))
        if index is not None and index > 0:
            by_index[index] = record
    tests = feedback.get("tests")
    recorded_end = max(by_index, default=0)
    passed = measurement(tests.get("passed")) if isinstance(tests, dict) else None
    failed = measurement(tests.get("failed_test")) if isinstance(tests, dict) else None
    last_executed = max(recorded_end, passed or 0, failed or 0)
    total = measurement(tests.get("total")) if isinstance(tests, dict) else None
    total = max(total or 0, last_executed)
    verdicts = {verdict.value for verdict in Verdict}
    rows = []
    for index in range(1, total + 1):
        record = by_index.get(index)
        status = "NOT_RUN" if index > last_executed else "NOT_RECORDED"
        if (
            record is not None
            and isinstance(record.get("verdict"), str)
            and record["verdict"] in verdicts
        ):
            status = record["verdict"]
        rows.append(
            {
                "test_index": index,
                "status": status,
                "time_ms": measurement(record.get("time_ms")) if record else None,
                "memory_kb": measurement(record.get("memory_kb")) if record else None,
            }
        )
    return rows


def submission_testcase_sections(
    submission: Submission,
    feedback: dict[str, object],
    *,
    allow_hidden: bool = False,
    show_previews: bool = True,
) -> list[dict[str, object]]:
    """Group result rows and attach authorized, byte-bounded data previews."""
    rows = submission_testcase_rows(feedback)
    if not rows:
        return []
    problem = submission.problem
    current_revision = submission.problem_revision == problem.revision
    cases = list(problem.testcases) if current_revision else []
    case_types = feedback.get("testcase_types")
    failure = feedback.get("failure")
    sample_rows, hidden_rows = [], []
    for row in rows:
        index = row["test_index"]
        case = cases[index - 1] if index <= len(cases) else None
        if isinstance(case_types, list) and index <= len(case_types):
            is_sample = case_types[index - 1] == "sample"
        else:
            is_sample = case is not None and case.type == "sample"
        failed = isinstance(failure, dict) and failure.get("test_index") == index
        if failed and failure.get("is_sample") is True:
            is_sample = True
        preview = None
        if show_previews and (is_sample or allow_hidden):
            if failed and "input" in failure:
                preview = {
                    key: failure[key]
                    for key in (
                        "input",
                        "expected",
                        "actual",
                        "input_truncated",
                        "expected_truncated",
                        "actual_truncated",
                    )
                    if key in failure
                }
            elif case is not None and (allow_hidden or case.type == "sample"):
                # A preview never reads or embeds the whole testcase file.
                try:
                    preview = {}
                    for key, path in (
                        ("input", case.input_path),
                        ("expected", case.output_path),
                    ):
                        with _testcase_path(case, path).open("rb") as stream:
                            raw = stream.read(PREVIEW_BYTES + 1)
                            preview[key] = raw[:PREVIEW_BYTES].decode(
                                "utf-8", errors="ignore"
                            )
                            preview[key + "_truncated"] = len(raw) > PREVIEW_BYTES
                except (OSError, ValueError):
                    preview = None
        row["preview"] = preview
        (sample_rows if is_sample else hidden_rows).append(row)
    sections = [
        {"id": "sample", "title": "Sample results", "rows": sample_rows},
        {"id": "testcase", "title": "Testcase results", "rows": hidden_rows},
    ]
    return [section for section in sections if section["rows"]]


def submission_feedback(
    submission: Submission,
    policy: str,
    *,
    allow_hidden: bool = False,
    include_success_compile: bool = False,
) -> dict[str, object]:
    """Construct an allowlist shared by Web, history and HTTP. Never copy a Judge dict."""
    if policy not in {"full", "diagnostic", "verdict_only"}:
        raise ValueError("Invalid feedback policy")
    if submission.status != SubmissionStatus.FINISHED.value:
        return {
            "status": submission.status,
            "verdict": None,
            "summary": "Judging is still in progress.",
        }

    stored = _object(submission.judge_result)
    verdict = submission.verdict if submission.verdict in VERDICT_MESSAGES else "IE"
    result = {
        "status": "FINISHED",
        "verdict": verdict,
        "summary": VERDICT_MESSAGES[verdict],
    }
    tests = _object(stored.get("tests"))
    failure = _object(stored.get("failure"))
    failed = _number(tests.get("failed_test"), minimum=1) or _number(
        failure.get("test_index"), minimum=1
    )
    if failed is not None:
        result["failure"] = {"test_index": failed}
    if policy == "verdict_only":
        return result
    if (
        _number(tests.get("total")) is not None
        and _number(tests.get("passed")) is not None
    ):
        result["tests"] = {
            "total": tests["total"],
            "passed": tests["passed"],
            "failed_test": failed,
        }
    resources = _object(stored.get("resources"))
    if _number(resources.get("time_ms")) is not None:
        result["resources"] = {
            "time_ms": resources["time_ms"],
            "memory_kb": _number(resources.get("memory_kb")),
        }
    limits = _object(stored.get("limits"))
    if all(_number(limits.get(key)) is not None for key in ("time_ms", "memory_mb")):
        result["limits"] = {key: limits[key] for key in ("time_ms", "memory_mb")}
    if isinstance(stored.get("test_results"), list):
        result["test_results"] = []
        for record in stored["test_results"]:
            if (
                not isinstance(record, dict)
                or _number(record.get("test_index"), minimum=1) is None
            ):
                continue
            item = {
                "test_index": record["test_index"],
                "verdict": record.get("verdict")
                if isinstance(record.get("verdict"), str)
                and record.get("verdict") in VERDICT_MESSAGES
                else None,
                "time_ms": _number(record.get("time_ms")),
                "memory_kb": _number(record.get("memory_kb")),
            }
            for key in (
                "timed_out",
                "output_exceeded",
                "oom_killed",
                "stdout_truncated",
                "stderr_truncated",
            ):
                if key in record:
                    item[key] = record[key] is True
            if type(record.get("exit_code")) is int:
                item["exit_code"] = record["exit_code"]
            result["test_results"].append(item)
    if isinstance(stored.get("testcase_types"), list):
        result["testcase_types"] = [
            kind
            if isinstance(kind, str) and kind in {"sample", "hidden", "generated"}
            else "unknown"
            for kind in stored["testcase_types"]
        ]
    compile_result = _object(submission.compile_result)
    if (
        verdict != "IE"
        and compile_result
        and (
            verdict == "CE"
            or compile_result.get("success") is False
            or include_success_compile
        )
    ):
        result["compile"] = compile_feedback(
            compile_result, include_text=compile_result.get("success") is not True
        )
    if policy == "full" and failed is not None and verdict != "IE":
        is_sample = failure.get("is_sample") is True
        if "is_sample" not in failure:
            is_sample = any(
                sample.input == failure.get("input")
                and sample.output == failure.get("expected")
                for sample in submission.problem.samples
            )
        if is_sample or allow_hidden:
            result["failure"]["is_sample"] = is_sample
            for key in ("input", "expected", "actual", "stderr"):
                if isinstance(failure.get(key), str):
                    clip = safe_diagnostic if key == "stderr" else bounded_text
                    result["failure"][key], result["failure"][key + "_truncated"] = (
                        clip(
                            failure[key],
                            truncated=failure.get(key + "_truncated") is True
                            or stored.get(
                                ("stdout" if key == "actual" else key) + "_truncated"
                            )
                            is True,
                        )
                    )
    for key in ("stdout_truncated", "stderr_truncated"):
        if key in stored:
            result[key] = stored[key] is True
    return result


def feedback_response(
    submission: Submission, policy: str, *, allow_hidden=False
) -> dict:
    result = submission_feedback(submission, policy, allow_hidden=allow_hidden)
    failure = result.get("failure")
    return {
        **result,
        "submission_id": submission.id,
        "feedback_mode": policy,
        "failed_test": failure.get("test_index") if failure else None,
        "compile": result.get("compile"),
        "execution": result.get("resources"),
        "diagnostic": {"message": result["summary"], "message_truncated": False}
        if policy != "verdict_only"
        else None,
        "summary_truncated": False,
    }
