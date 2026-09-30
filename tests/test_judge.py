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
    assert result["verdict"] == "AC"
    assert transitions == ["RUNNING"]
    assert result["resources"]["memory_kb"] == 4096


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
