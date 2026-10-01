import os
import subprocess
import sys

import pytest

from minioj.config import PROJECT_ROOT, Settings


def test_job_dir_defaults_to_data_dir_for_compatibility(tmp_path):
    configured = Settings(data_dir=tmp_path / "data", job_dir=None)

    assert configured.jobs_dir == tmp_path / "data" / "jobs"


def test_job_dir_can_be_configured_independently(monkeypatch):
    monkeypatch.setenv("MINIOJ_JOB_DIR", "runtime/jobs")

    configured = Settings()

    assert configured.jobs_dir == PROJECT_ROOT / "runtime" / "jobs"


@pytest.mark.parametrize(
    "secret_key",
    [
        "",
        "too-short",
        "development-only-change-this-secret-key",
        "replace-with-a-long-random-secret",
    ],
)
def test_server_rejects_missing_short_or_placeholder_secret(secret_key):
    configured = Settings(secret_key=secret_key)

    with pytest.raises(RuntimeError, match="MINIOJ_SECRET_KEY"):
        configured.validate_server()


def test_server_accepts_non_placeholder_secret_with_minimum_length():
    Settings(secret_key="a" * 32).validate_server()


@pytest.mark.parametrize(
    "field",
    [
        "testcase_file_limit_bytes",
        "output_limit_bytes",
        "source_limit_bytes",
        "stdin_limit_bytes",
        "compile_time_limit_ms",
        "compile_memory_mb",
        "testcase_build_time_limit_ms",
        "testcase_build_memory_mb",
        "generator_max_cases",
        "max_queued_submissions",
        "max_queued_runs",
        "custom_run_wait_seconds",
        "overload_retry_after_seconds",
        "token_default_days",
    ],
)
def test_server_rejects_non_positive_testcase_build_limits(field):
    configured = Settings(secret_key="a" * 32, **{field: 0})

    with pytest.raises(RuntimeError, match="positive integers"):
        configured.validate_server()


def test_secret_validation_error_does_not_disclose_secret():
    secret = "short-secret"

    with pytest.raises(RuntimeError) as error:
        Settings(secret_key=secret).validate_server()

    assert secret not in str(error.value)


def test_server_rejects_unknown_feedback_policy():
    with pytest.raises(RuntimeError, match="MINIOJ_FEEDBACK_POLICY"):
        Settings(secret_key="a" * 32, feedback_policy="everything").validate_server()


def test_worker_rejects_empty_owner_label():
    with pytest.raises(RuntimeError, match="MINIOJ_WORKER_OWNER"):
        Settings(worker_owner=" ").validate_worker()


def test_server_import_rejects_example_secret_without_disclosing_it():
    placeholder = "replace-with-a-long-random-secret"
    environment = {**os.environ, "MINIOJ_SECRET_KEY": placeholder}

    result = subprocess.run(
        [sys.executable, "-c", "import minioj.server.main"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "MINIOJ_SECRET_KEY must be set" in result.stderr
    assert placeholder not in result.stderr
