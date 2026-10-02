from __future__ import annotations

import json
import subprocess

import pytest

from minioj.judge.runner import DockerJudge, InfrastructureError


def mount_spec(command: list[str]) -> str:
    return command[command.index("--mount") + 1]


def test_container_command_uses_valid_long_mount_syntax():
    judge = DockerJudge("test-image")
    writable = judge._container_command(
        "compile", 512, "/tmp/job", ["g++"], read_only_mount=False
    )
    read_only = judge._container_command(
        "run", 64, "/tmp/job", ["/work/main"], read_only_mount=True
    )

    assert writable[1] == "create"
    assert "--interactive" in writable
    assert writable[writable.index("--label") + 1] == "minioj.owner=request"
    assert mount_spec(writable) == "type=bind,source=/tmp/job,target=/work"
    assert mount_spec(read_only) == "type=bind,source=/tmp/job,target=/work,readonly"
    assert "--preserve-status" not in writable


def test_container_creation_failure_is_an_infrastructure_error(monkeypatch):
    judge = DockerJudge("test-image")
    command = judge._container_command(
        "broken", 64, "/missing/job", ["true"], read_only_mount=False
    )

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            command, 125, stdout="", stderr="invalid mount configuration"
        ),
    )

    with pytest.raises(InfrastructureError, match="invalid mount configuration"):
        judge._run_limited(command, "broken", "", 1000, 1024)


class _FinishedProcess:
    returncode = 0

    def poll(self):
        return 0


def test_short_process_output_is_limited_after_exit(monkeypatch):
    judge = DockerJudge("test-image")

    def fake_run(command, **_kwargs):
        if command[1] == "create":
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id\n", stderr=""
            )
        if command[1] == "inspect":
            state = {"ExitCode": 0, "OOMKilled": False, "Error": ""}
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(state), stderr=""
            )
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    def fake_popen(_command, *, stdout, stderr, **_kwargs):
        stdout.write(b"a" * 1500)
        stderr.write(b"b" * 500)
        return _FinishedProcess()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(judge, "_read_container_memory_peak", lambda _id: None)
    result = judge._run_limited(["docker", "create"], "short-output", "", 1000, 1024)
    assert result.output_exceeded is True
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 1024
    assert result.memory_kb is None


def test_cleanup_failure_is_an_infrastructure_error(monkeypatch):
    judge = DockerJudge("test-image")

    def fake_run(command, **_kwargs):
        if command[1] == "create":
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id\n", stderr=""
            )
        if command[1] == "inspect":
            state = {"ExitCode": 0, "OOMKilled": False, "Error": ""}
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(state), stderr=""
            )
        if command[1] == "rm":
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="resource busy"
            )
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_args, **_kwargs: _FinishedProcess()
    )
    monkeypatch.setattr(judge, "_read_container_memory_peak", lambda _id: None)
    with pytest.raises(InfrastructureError, match="could not remove container"):
        judge._run_limited(["docker", "create"], "not-removed", "", 1000, 1024)


def test_owned_stale_containers_are_removed(monkeypatch):
    judge = DockerJudge("test-image", owner="worker")
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[1] == "ps":
            return subprocess.CompletedProcess(
                command, 0, stdout="old-one\nold-two\n", stderr=""
            )
        if command[1] == "rm":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    judge.cleanup_owned_containers()
    assert calls[0][-1] == "label=minioj.owner=worker"
    assert calls[1] == ["docker", "rm", "--force", "old-one", "old-two"]


@pytest.mark.parametrize("cpu_ms, wall_ms", [(2, 250), (85, 500)])
def test_execution_uses_trusted_cpu_report_not_host_elapsed(
    monkeypatch, tmp_path, cpu_ms, wall_ms
):
    report = tmp_path / "result.json"
    report.write_text(
        json.dumps(
            {
                "cpu_time_ms": cpu_ms,
                "wall_time_ms": wall_ms,
                "exit_code": 0,
                "timed_out": False,
                "setup_error": False,
                "memory_kb": 4096,
            }
        )
    )
    removed = []

    def fake_run(command, **kwargs):
        if command[1] == "create":
            return subprocess.CompletedProcess(
                command, 0, stdout="container-id", stderr=""
            )
        if command[1] == "inspect":
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"ExitCode": 0}), stderr=""
            )
        if command[1] == "rm":
            removed.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _FinishedProcess())
    judge = DockerJudge("image")
    monkeypatch.setattr(judge, "_read_container_memory_peak", lambda _id: None)
    result = judge._run_limited(
        ["docker", "create"], "cpu", "", 10000, 1024, metrics_path=report
    )
    assert result.time_ms == cpu_ms
    assert result.wall_time_ms == wall_ms
    assert result.memory_kb == 4096
    assert removed


def test_execution_supervisor_has_separate_report_mount(tmp_path):
    command = DockerJudge("image")._container_command(
        "cpu",
        128,
        "/job",
        ["supervisor"],
        read_only_mount=True,
        metrics_path=tmp_path / "report.json",
    )
    assert command[command.index("--user") + 1] == "0:0"
    assert "type=bind,source=/job,target=/work,readonly" in command
    assert (
        f"type=bind,source={tmp_path}/report.json,target=/minioj-result.json" in command
    )
    assert "--privileged" not in command
    assert "stack=134217728:134217728" in command


def test_worker_rejects_an_old_image_without_cpu_accounting(monkeypatch):
    monkeypatch.setattr(
        "minioj.judge.runner.shutil.which", lambda _name: "/usr/bin/docker"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0], 0, stdout="<no value>\n", stderr=""
        ),
    )
    with pytest.raises(InfrastructureError, match="CPU supervisor"):
        DockerJudge("old-image").ensure_available()


@pytest.mark.parametrize("valid_report", [True, False])
def test_docker_transport_timeout_is_not_a_user_tle(
    monkeypatch, tmp_path, valid_report
):
    report = tmp_path / "result.json"
    report.write_text(
        json.dumps(
            {
                "cpu_time_ms": 2,
                "wall_time_ms": 2,
                "exit_code": 0,
                "timed_out": False,
                "setup_error": False,
                "memory_kb": None,
            }
        )
        if valid_report
        else ""
    )

    class HungAttach:
        returncode = 0

        def poll(self):
            return None

        def wait(self, **kwargs):
            return 0

    removed = []

    def fake_run(command, **kwargs):
        if command[1] == "inspect":
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps({"ExitCode": 0}), stderr=""
            )
        if command[1] == "rm":
            removed.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="container-id", stderr="")

    ticks = iter([0.0, 10.0, 10.0])
    monkeypatch.setattr("minioj.judge.runner.time.monotonic", lambda: next(ticks))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: HungAttach())
    judge = DockerJudge("image")
    monkeypatch.setattr(judge, "_read_container_memory_peak", lambda _id: None)
    monkeypatch.setattr(judge, "_read_container_cpu_ms", lambda _id: 2)
    with pytest.raises(InfrastructureError):
        judge._run_limited(
            ["docker", "create"], "watchdog", "", 1000, 1024, metrics_path=report
        )
    assert removed
