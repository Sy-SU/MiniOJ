from __future__ import annotations

import pytest

from minioj.config import settings
from minioj.judge.runner import DockerJudge, ProcessResult


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
