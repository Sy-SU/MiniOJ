from __future__ import annotations

import pytest

from minioj.config import settings
from minioj.judge.runner import DockerJudge, ProcessResult
from minioj.judge.runner import TestcaseBuildError as BuildError


def process(**overrides) -> ProcessResult:
    values = {"exit_code": 0, "stdout": "3\n", "stderr": "", "time_ms": 7}
    values.update(overrides)
    return ProcessResult(**values)


def configured_judge(
    monkeypatch, execution: ProcessResult, compile_result: ProcessResult | None = None
):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    judge = DockerJudge("test-image")
    monkeypatch.setattr(judge, "ensure_available", lambda: None)
    monkeypatch.setattr(
        judge,
        "compile",
        lambda _job, _source, _memory: compile_result or process(stdout=""),
    )
    monkeypatch.setattr(
        judge, "execute", lambda _job, _stdin, _time, _memory: execution
    )
    return judge


def test_judge_accepts_normalized_output_and_calls_running_hook(monkeypatch):
    judge = configured_judge(monkeypatch, process(stdout="3  \n\n", memory_kb=4096))
    transitions: list[str] = []
    compile_data, result = judge.judge(
        "int main(){}",
        [("1 2\n", "3\n")],
        1000,
        64,
        lambda: transitions.append("RUNNING"),
    )
    assert compile_data["success"] is True
    assert compile_data == {
        "success": True,
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "time_ms": 7,
        "memory_kb": None,
        "timed_out": False,
        "output_exceeded": False,
        "oom_killed": False,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "output_truncated": False,
    }
    assert result["verdict"] == "AC"
    assert transitions == ["RUNNING"]
    assert result["resources"]["memory_kb"] == 4096
    assert result["test_results"] == [
        {
            "test_index": 1,
            "verdict": "AC",
            "exit_code": 0,
            "time_ms": 7,
            "memory_kb": 4096,
            "timed_out": False,
            "output_exceeded": False,
            "oom_killed": False,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }
    ]


@pytest.mark.parametrize(
    ("execution", "expected"),
    [
        (process(stdout="4\n"), "WA"),
        (process(exit_code=1, stderr="boom"), "RE"),
        (process(timed_out=True), "TLE"),
        (process(oom_killed=True, exit_code=137), "MLE"),
        (process(output_exceeded=True), "OLE"),
    ],
)
def test_judge_maps_execution_failures(monkeypatch, execution, expected):
    judge = configured_judge(monkeypatch, execution)
    _, result = judge.judge("int main(){}", [("1 2\n", "3\n")], 1000, 64)
    assert result["verdict"] == expected
    assert result["failure"]["test_index"] == 1


def test_judge_reports_compile_error_without_running(monkeypatch):
    compile_failure = process(exit_code=1, stdout="", stderr="syntax error")
    judge = configured_judge(monkeypatch, process(), compile_failure)
    _, result = judge.judge("broken", [("", "")], 1000, 64)
    assert result["verdict"] == "CE"
    assert result["summary"] == "Compilation failed."


def test_compile_output_limit_is_a_truncated_compile_error(monkeypatch):
    compile_failure = process(
        stdout="diagnostic",
        output_exceeded=True,
        stdout_truncated=True,
    )
    judge = configured_judge(monkeypatch, process(), compile_failure)
    compile_data, result = judge.judge("broken", [("", "")], 1000, 64)
    assert compile_data["success"] is False
    assert compile_data["output_exceeded"] is True
    assert compile_data["stdout_truncated"] is True
    assert compile_data["output_truncated"] is True
    assert result["verdict"] == "CE"


@pytest.mark.parametrize("exit_code", [124, 137])
def test_fast_user_exit_codes_are_runtime_errors_not_timeouts(monkeypatch, exit_code):
    judge = DockerJudge("test-image")
    monkeypatch.setattr(
        judge,
        "_run_limited",
        lambda *_args, **_kwargs: process(exit_code=exit_code, time_ms=5),
    )
    result = judge.execute(settings.jobs_dir, "", 1000, 64)
    assert result.exit_code == exit_code
    assert result.timed_out is False


@pytest.mark.parametrize("exit_code", [137, 143])
def test_timeout_wrapper_status_is_tle_at_the_limit(monkeypatch, exit_code):
    judge = DockerJudge("test-image")
    monkeypatch.setattr(
        judge,
        "_run_limited",
        lambda *_args, **_kwargs: process(exit_code=exit_code, time_ms=1000),
    )
    result = judge.execute(settings.jobs_dir, "", 1000, 64)
    assert result.timed_out is True


def test_missing_memory_sample_is_preserved_as_unknown(monkeypatch):
    judge = configured_judge(monkeypatch, process(stdout="3\n", memory_kb=None))
    _, result = judge.judge("int main(){}", [("1 2\n", "3\n")], 1000, 64)
    assert result["resources"]["memory_kb"] is None


def test_testcase_builder_runs_seeded_generator_then_standard_solution(monkeypatch):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    judge = DockerJudge("test-image")
    monkeypatch.setattr(judge, "ensure_available", lambda: None)
    compiled_sources: list[str] = []
    monkeypatch.setattr(
        judge,
        "compile",
        lambda _directory, source, _memory: (
            compiled_sources.append(source) or process(stdout="")
        ),
    )
    calls: list[tuple[str, list[str] | None]] = []

    def execute(
        directory,
        stdin_data,
        _time_limit,
        _memory_limit,
        *,
        arguments=None,
        output_limit=None,
    ):
        assert output_limit == settings.testcase_file_limit_bytes
        calls.append((stdin_data, arguments))
        if arguments:
            return process(stdout=f"{arguments[0]} {arguments[1]}\n")
        left, right = map(int, stdin_data.split())
        return process(stdout=f"{left + right}\n")

    monkeypatch.setattr(judge, "execute", execute)
    cases = judge.build_testcases(
        "standard source",
        generator_source="generator source",
        case_count=2,
        base_seed=9,
    )

    assert compiled_sources == ["standard source", "generator source"]
    assert cases == [("9 1\n", "10\n"), ("10 2\n", "12\n")]
    assert calls == [
        ("", ["9", "1"]),
        ("", ["10", "2"]),
        ("9 1\n", None),
        ("10 2\n", None),
    ]


def test_testcase_builder_rejects_generator_output_failure(monkeypatch):
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    judge = DockerJudge("test-image")
    monkeypatch.setattr(judge, "ensure_available", lambda: None)
    monkeypatch.setattr(judge, "compile", lambda *_args, **_kwargs: process(stdout=""))
    monkeypatch.setattr(
        judge,
        "execute",
        lambda *_args, **_kwargs: process(stdout="x", output_exceeded=True),
    )

    with pytest.raises(BuildError, match="Generator case 1 exceeded"):
        judge.build_testcases(
            "standard source",
            generator_source="generator source",
            case_count=1,
        )
