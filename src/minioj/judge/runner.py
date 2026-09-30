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


class TestcaseBuildError(RuntimeError):
    pass


@dataclass
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str
    time_ms: int
    memory_kb: int = 0
    timed_out: bool = False
    output_exceeded: bool = False
    oom_killed: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    stdout_valid_utf8: bool = True
    stderr_valid_utf8: bool = True


class DockerJudge:
    def __init__(self, image: str | None = None) -> None:
        self.image = image or settings.docker_image

    def ensure_available(self) -> None:
        if shutil.which("docker") is None:
            raise InfrastructureError("Docker CLI is not installed or is not on PATH.")
        try:
            check = subprocess.run(
                ["docker", "image", "inspect", self.image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InfrastructureError(
                f"Could not inspect Docker judge image: {exc}"
            ) from exc
        if check.returncode != 0:
            raise InfrastructureError(
                f"Judge image {self.image!r} is unavailable. Build it with: "
                "docker build -t minioj-cpp20:latest docker/cpp20"
            )

    @staticmethod
    def _remove_container(name: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _container_command(
        self,
        name: str,
        memory_mb: int,
        mount: str,
        command: list[str],
        *,
        read_only_mount: bool,
    ) -> list[str]:
        mount_spec = f"type=bind,source={mount},target=/work"
        if read_only_mount:
            mount_spec += ",readonly"
        return [
            "docker",
            "create",
            "--name",
            name,
            "--interactive",
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
            mount_spec,
            self.image,
            *command,
        ]

    @staticmethod
    def _read_container_memory_peak(container_id: str | None) -> int:
        if not container_id:
            return 0
        candidates = (
            Path(
                f"/sys/fs/cgroup/system.slice/docker-{container_id}.scope/memory.peak"
            ),
            Path(f"/sys/fs/cgroup/docker/{container_id}/memory.peak"),
        )
        for path in candidates:
            try:
                return max(0, int(path.read_text(encoding="utf-8").strip()) // 1024)
            except (OSError, ValueError):
                continue
        return 0

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
            try:
                creation = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._remove_container(name)
                raise InfrastructureError(
                    f"Could not create Docker container: {exc}"
                ) from exc
            if creation.returncode != 0:
                detail = creation.stderr.strip() or creation.stdout.strip()
                self._remove_container(name)
                raise InfrastructureError(
                    f"Docker could not create the judge container: {detail[:2000]}"
                )
            container_id = creation.stdout.strip()
            if not container_id:
                self._remove_container(name)
                raise InfrastructureError("Docker create returned no container id.")
            started = time.monotonic()
            try:
                process = subprocess.Popen(
                    ["docker", "start", "--attach", "--interactive", name],
                    stdin=input_file,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
            except OSError as exc:
                self._remove_container(name)
                raise InfrastructureError(
                    f"Could not start Docker container: {exc}"
                ) from exc
            timed_out = False
            output_exceeded = False
            memory_peak_kb = 0
            while process.poll() is None:
                memory_peak_kb = max(
                    memory_peak_kb, self._read_container_memory_peak(container_id)
                )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                if elapsed_ms > timeout_ms:
                    timed_out = True
                    break
                if stdout_file.tell() + stderr_file.tell() > output_limit:
                    output_exceeded = True
                    break
                time.sleep(0.002)
            memory_peak_kb = max(
                memory_peak_kb, self._read_container_memory_peak(container_id)
            )
            infrastructure_error: str | None = None
            if timed_out or output_exceeded:
                try:
                    subprocess.run(
                        ["docker", "kill", name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    infrastructure_error = f"Could not stop Docker container: {exc}"
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            exit_code = process.returncode or 0
            elapsed_ms = int((time.monotonic() - started) * 1000)
            oom_killed = False
            try:
                inspect = subprocess.run(
                    ["docker", "inspect", "-f", "{{json .State}}", name],
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                infrastructure_error = f"Could not inspect Docker container: {exc}"
            else:
                if inspect.returncode == 0:
                    try:
                        state = json.loads(inspect.stdout)
                    except (ValueError, TypeError):
                        infrastructure_error = (
                            "Docker returned an invalid container state."
                        )
                    else:
                        state_error = str(state.get("Error") or "").strip()
                        if state_error:
                            infrastructure_error = state_error
                        oom_killed = bool(state.get("OOMKilled"))
                        exit_code = int(state.get("ExitCode", exit_code))
                else:
                    infrastructure_error = (
                        inspect.stderr.strip() or "Container was not created."
                    )
            self._remove_container(name)
            stdout_size = stdout_file.tell()
            stderr_size = stderr_file.tell()
            if stdout_size + stderr_size > output_limit:
                output_exceeded = True
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout_bytes = stdout_file.read(output_limit)
            remaining = max(0, output_limit - len(stdout_bytes))
            stderr_bytes = stderr_file.read(remaining)
            try:
                stdout = stdout_bytes.decode("utf-8")
                stdout_valid_utf8 = True
            except UnicodeDecodeError:
                stdout = stdout_bytes.decode("utf-8", errors="replace")
                stdout_valid_utf8 = False
            try:
                stderr = stderr_bytes.decode("utf-8")
                stderr_valid_utf8 = True
            except UnicodeDecodeError:
                stderr = stderr_bytes.decode("utf-8", errors="replace")
                stderr_valid_utf8 = False
            if infrastructure_error:
                detail = infrastructure_error
                if stderr.strip():
                    detail = f"{detail}: {stderr.strip()}"
                raise InfrastructureError(
                    f"Docker infrastructure failure: {detail[:2000]}"
                )
            return ProcessResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                time_ms=elapsed_ms,
                memory_kb=memory_peak_kb,
                timed_out=timed_out,
                output_exceeded=output_exceeded,
                oom_killed=oom_killed,
                stdout_truncated=stdout_size > len(stdout.encode()),
                stderr_truncated=stderr_size > len(stderr.encode()),
                stdout_valid_utf8=stdout_valid_utf8,
                stderr_valid_utf8=stderr_valid_utf8,
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
        self,
        job_dir: Path,
        stdin_data: str,
        time_limit_ms: int,
        memory_limit_mb: int,
        *,
        arguments: list[str] | None = None,
        output_limit: int | None = None,
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
                *(arguments or []),
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
            output_limit or settings.output_limit_bytes,
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
                    "memory_kb": compiled.memory_kb,
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
                "memory_kb": result.memory_kb,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
            }
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    @staticmethod
    def _require_build_success(result: ProcessResult, label: str) -> str:
        if result.timed_out:
            reason = "timed out"
        elif result.output_exceeded:
            reason = "exceeded the output limit"
        elif result.oom_killed:
            reason = "exceeded the memory limit"
        elif result.exit_code != 0:
            reason = f"exited with code {result.exit_code}"
        elif not result.stdout_valid_utf8:
            reason = "produced output that is not valid UTF-8"
        else:
            return result.stdout
        detail = result.stderr.strip()
        suffix = f": {detail[:2000]}" if detail else ""
        raise TestcaseBuildError(f"{label} {reason}{suffix}")

    def build_testcases(
        self,
        standard_source: str,
        *,
        input_data: str | None = None,
        generator_source: str | None = None,
        case_count: int = 1,
        base_seed: int = 1,
    ) -> list[tuple[str, str]]:
        if generator_source is not None:
            if not 1 <= case_count <= settings.generator_max_cases:
                raise TestcaseBuildError("Generator case count is outside the limit")
            if not 0 <= base_seed <= 2_147_483_647:
                raise TestcaseBuildError("Generator base seed is outside the limit")
            if base_seed + case_count - 1 > 2_147_483_647:
                raise TestcaseBuildError("Generator seed range is outside the limit")
        self.ensure_available()
        root = Path(tempfile.mkdtemp(prefix="testcase-build-", dir=settings.jobs_dir))
        standard_dir = root / "standard"
        generator_dir = root / "generator"
        standard_dir.mkdir()
        try:
            compiled = self.compile(
                standard_dir, standard_source, settings.testcase_build_memory_mb
            )
            self._require_build_success(compiled, "Standard solution compilation")
            inputs: list[str]
            if generator_source is None:
                if input_data is None:
                    raise TestcaseBuildError("Uploaded testcase input is missing")
                inputs = [input_data]
            else:
                generator_dir.mkdir()
                compiled_generator = self.compile(
                    generator_dir,
                    generator_source,
                    settings.testcase_build_memory_mb,
                )
                self._require_build_success(compiled_generator, "Generator compilation")
                inputs = []
                for index in range(1, case_count + 1):
                    seed = base_seed + index - 1
                    generated = self.execute(
                        generator_dir,
                        "",
                        settings.testcase_build_time_limit_ms,
                        settings.testcase_build_memory_mb,
                        arguments=[str(seed), str(index)],
                        output_limit=settings.testcase_file_limit_bytes,
                    )
                    inputs.append(
                        self._require_build_success(
                            generated, f"Generator case {index}"
                        )
                    )
            cases: list[tuple[str, str]] = []
            for index, generated_input in enumerate(inputs, start=1):
                standard = self.execute(
                    standard_dir,
                    generated_input,
                    settings.testcase_build_time_limit_ms,
                    settings.testcase_build_memory_mb,
                    output_limit=settings.testcase_file_limit_bytes,
                )
                expected = self._require_build_success(
                    standard, f"Standard solution case {index}"
                )
                cases.append((generated_input, expected))
            return cases
        finally:
            shutil.rmtree(root, ignore_errors=True)

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
                    "resources": {
                        "time_ms": compiled.time_ms,
                        "memory_kb": compiled.memory_kb,
                    },
                }
            if on_compiled is not None:
                on_compiled()
            max_time = 0
            max_memory = 0
            passed = 0
            for index, (input_data, expected) in enumerate(tests, start=1):
                result = self.execute(
                    job_dir, input_data, time_limit_ms, memory_limit_mb
                )
                max_time = max(max_time, result.time_ms)
                max_memory = max(max_memory, result.memory_kb)
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
                        "resources": {"time_ms": max_time, "memory_kb": max_memory},
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
                "resources": {"time_ms": max_time, "memory_kb": max_memory},
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
