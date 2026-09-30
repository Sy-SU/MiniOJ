from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from minioj.config import settings
from minioj.judge.checker import outputs_match


class InfrastructureError(RuntimeError):
    pass


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    time_ms: int
    timed_out: bool = False
    output_exceeded: bool = False
    oom_killed: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class DockerJudge:
    def __init__(self, image: str | None = None) -> None:
        self.image = image or settings.docker_image

    def ensure_available(self) -> None:
        if shutil.which("docker") is None:
            raise InfrastructureError("Docker CLI is not installed or is not on PATH.")
        check = subprocess.run(
            ["docker", "image", "inspect", self.image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
        if check.returncode != 0:
            raise InfrastructureError(
                f"Judge image {self.image!r} is unavailable. Build it with: "
                "docker build -t minioj-cpp20:latest docker/cpp20"
            )

    def _container_command(
        self,
        name: str,
        memory_mb: int,
        mount: str,
        command: list[str],
        *,
        read_only_mount: bool,
    ) -> list[str]:
        mode = "ro" if read_only_mount else "rw"
        return [
            "docker",
            "run",
            "--name",
            name,
            "--network",
            "none",
            "--cpus",
            "1.0",
            "--memory",
            f"{memory_mb}m",
            "--memory-swap",
            f"{memory_mb}m",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",
            "--user",
            "1000:1000",
            "--workdir",
            "/work",
            "--mount",
            f"type=bind,source={mount},target=/work,{mode}",
            self.image,
            *command,
        ]

    def _run_limited(
        self,
        cmd: list[str],
        name: str,
        stdin_data: str,
        timeout_ms: int,
        output_limit: int,
    ) -> ProcessResult:
        with (
            tempfile.TemporaryFile() as input_file,
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            input_file.write(stdin_data.encode())
            input_file.seek(0)
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    cmd,
                    stdin=input_file,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except OSError as exc:
                raise InfrastructureError(f"Could not start Docker: {exc}") from exc
            timed_out = False
            output_exceeded = False
            while process.poll() is None:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > timeout_ms:
                    timed_out = True
                    break
                if stdout_file.tell() + stderr_file.tell() > output_limit:
                    output_exceeded = True
                    break
                time.sleep(0.01)
            if timed_out or output_exceeded:
                subprocess.run(
                    ["docker", "kill", name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                    check=False,
                )
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            exit_code = process.returncode or 0
            elapsed_ms = int((time.monotonic() - started) * 1000)
            inspect = subprocess.run(
                ["docker", "inspect", "-f", "{{json .State}}", name],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            oom_killed = False
            if inspect.returncode == 0:
                try:
                    state = json.loads(inspect.stdout)
                    oom_killed = bool(state.get("OOMKilled"))
                    exit_code = int(state.get("ExitCode", exit_code))
                except (ValueError, TypeError):
                    pass
            subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read(output_limit).decode(errors="replace")
            remaining = max(0, output_limit - len(stdout.encode()))
            stderr = stderr_file.read(remaining).decode(errors="replace")
            return ProcessResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                time_ms=elapsed_ms,
                timed_out=timed_out,
                output_exceeded=output_exceeded,
                oom_killed=oom_killed,
                stdout_truncated=stdout_size > len(stdout.encode()),
                stderr_truncated=stderr_size > len(stderr.encode()),
            )

    def compile(self, job_dir: Path, source_code: str, memory_mb: int) -> ProcessResult:
        source = job_dir / "main.cpp"
        source.write_text(source_code, encoding="utf-8")
        source.chmod(0o644)
        job_dir.chmod(0o777)
        name = "minioj-compile-" + uuid.uuid4().hex
        cmd = self._container_command(
            name,
            max(memory_mb, 512),
            str(job_dir),
            ["g++", "-std=c++20", "-O2", "-pipe", "-o", "main", "main.cpp"],
            read_only_mount=False,
        )
        result = self._run_limited(cmd, name, "", 30_000, settings.output_limit_bytes)
        executable = job_dir / "main"
        if result.exit_code == 0 and not executable.exists():
            raise InfrastructureError(
                "Compiler exited successfully but produced no executable."
            )
        return result

    def execute(
        self, job_dir: Path, stdin_data: str, time_limit_ms: int, memory_limit_mb: int
    ) -> ProcessResult:
        name = "minioj-run-" + uuid.uuid4().hex
        timeout_seconds = f"{time_limit_ms / 1000:.3f}s"
        cmd = self._container_command(
            name,
            memory_limit_mb,
            str(job_dir),
            [
                "timeout",
                "--signal=TERM",
                "--kill-after=0.2s",
                timeout_seconds,
                "/work/main",
            ],
            read_only_mount=True,
        )
        # GNU timeout measures only in-container execution. The host limit is a
        # fallback for Docker startup/daemon failures and includes a grace period.
        result = self._run_limited(
            cmd,
            name,
            stdin_data,
            time_limit_ms + 3000,
            settings.output_limit_bytes,
        )
        if (
            result.exit_code in {124, 137}
            and not result.oom_killed
            and not result.output_exceeded
        ):
            result.timed_out = True
        return result

    def custom_run(
        self,
        source_code: str,
        stdin_data: str,
        time_limit_ms: int = 3000,
        memory_limit_mb: int = 256,
    ) -> dict:
        self.ensure_available()
        job_dir = Path(tempfile.mkdtemp(prefix="run-", dir=settings.jobs_dir))
        try:
            compiled = self.compile(job_dir, source_code, memory_limit_mb)
            if (
                compiled.exit_code != 0
                or compiled.timed_out
                or compiled.output_exceeded
            ):
                return {
                    "status": "CE",
                    "stdout": "",
                    "stderr": compiled.stderr,
                    "exit_code": compiled.exit_code,
                    "time_ms": compiled.time_ms,
                    "memory_kb": 0,
                    "stdout_truncated": False,
                    "stderr_truncated": compiled.stderr_truncated,
                }
            result = self.execute(job_dir, stdin_data, time_limit_ms, memory_limit_mb)
            status = "OK"
            if result.timed_out:
                status = "TLE"
            elif result.output_exceeded:
                status = "OLE"
            elif result.oom_killed:
                status = "MLE"
            elif result.exit_code != 0:
                status = "RE"
            return {
                "status": status,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "time_ms": result.time_ms,
                "memory_kb": 0,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
            }
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def judge(
        self,
        source_code: str,
        tests: list[tuple[str, str]],
        time_limit_ms: int,
        memory_limit_mb: int,
        on_compiled: Callable[[], None] | None = None,
    ) -> tuple[dict, dict]:
        self.ensure_available()
        job_dir = Path(tempfile.mkdtemp(prefix="judge-", dir=settings.jobs_dir))
        try:
            compiled = self.compile(job_dir, source_code, memory_limit_mb)
            compile_result = {
                "success": compiled.exit_code == 0 and not compiled.timed_out,
                "stderr": compiled.stderr,
                "stderr_truncated": compiled.stderr_truncated,
                "time_ms": compiled.time_ms,
            }
            if not compile_result["success"]:
                return compile_result, {
                    "verdict": "CE",
                    "summary": "Compilation failed.",
                    "tests": {"total": len(tests), "passed": 0, "failed_test": None},
                    "resources": {"time_ms": compiled.time_ms, "memory_kb": 0},
                }
            if on_compiled is not None:
                on_compiled()
            max_time = 0
            passed = 0
            for index, (input_data, expected) in enumerate(tests, start=1):
                result = self.execute(
                    job_dir, input_data, time_limit_ms, memory_limit_mb
                )
                max_time = max(max_time, result.time_ms)
                verdict = None
                if result.timed_out:
                    verdict = "TLE"
                elif result.output_exceeded:
                    verdict = "OLE"
                elif result.oom_killed:
                    verdict = "MLE"
                elif result.exit_code != 0:
                    verdict = "RE"
                elif not outputs_match(result.stdout, expected):
                    verdict = "WA"
                if verdict:
                    failure = {
                        "test_index": index,
                        "input": input_data,
                        "expected": expected,
                        "actual": result.stdout,
                        "stderr": result.stderr,
                    }
                    return compile_result, {
                        "verdict": verdict,
                        "summary": self._summary(verdict, index),
                        "tests": {
                            "total": len(tests),
                            "passed": passed,
                            "failed_test": index,
                        },
                        "failure": failure,
                        "resources": {"time_ms": max_time, "memory_kb": 0},
                        "limits": {
                            "time_ms": time_limit_ms,
                            "memory_mb": memory_limit_mb,
                        },
                        "stdout_truncated": result.stdout_truncated,
                        "stderr_truncated": result.stderr_truncated,
                    }
                passed += 1
            return compile_result, {
                "verdict": "AC",
                "summary": f"Accepted. Passed {passed} test(s).",
                "tests": {"total": len(tests), "passed": passed, "failed_test": None},
                "resources": {"time_ms": max_time, "memory_kb": 0},
            }
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    @staticmethod
    def _summary(verdict: str, test_index: int) -> str:
        messages = {
            "WA": "Wrong answer",
            "RE": "Runtime error",
            "TLE": "Time limit exceeded",
            "MLE": "Memory limit exceeded",
            "OLE": "Output limit exceeded",
        }
        return f"{messages[verdict]} on test {test_index}."
