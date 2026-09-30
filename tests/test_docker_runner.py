from __future__ import annotations

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
    assert mount_spec(writable) == "type=bind,source=/tmp/job,target=/work"
    assert mount_spec(read_only) == "type=bind,source=/tmp/job,target=/work,readonly"


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
