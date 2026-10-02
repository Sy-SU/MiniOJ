from __future__ import annotations

import json

import pytest

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.judge import InfrastructureError, SubmissionStatus
from minioj.judge import TestcaseBuildStatus as BuildStatus
from minioj.models import CustomRun, Problem, Submission, User
from minioj.models import TestcaseBuild as BuildModel
from minioj.problems import add_testcase
from minioj.worker.main import (
    INTERRUPTED_BUILD_ERROR,
    INTERRUPTED_SUBMISSION_SUMMARY,
    claim_next_custom_run,
    claim_next_submission,
    cleanup_stale_job_directories,
    exclusive_worker,
    judge_submission,
    process_custom_run,
    recover_interrupted_work,
)


def _user_and_problem() -> tuple[int, str]:
    with SessionLocal() as db:
        user = User(
            username="worker_user",
            email="worker@example.com",
            password_hash="not-used-by-this-test",
        )
        db.add(user)
        db.flush()
        problem = Problem(
            id="worker-problem",
            title="Worker Problem",
            statement="Print one.",
            created_by=user.id,
        )
        db.add(problem)
        db.commit()
        return user.id, problem.id


def test_worker_maps_judge_infrastructure_failure_to_ie(monkeypatch):
    user_id, problem_id = _user_and_problem()
    with SessionLocal() as db:
        problem = db.get(Problem, problem_id)
        add_testcase(db, problem, "hidden", "", "1\n")
        submission = Submission(
            user_id=user_id,
            problem_id=problem.id,
            language="cpp20",
            source_code="int main() {}",
            status="COMPILING",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    def fail_judge(*_args, **_kwargs):
        raise InfrastructureError("daemon unavailable")

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", fail_judge)
    judge_submission(submission_id)

    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        result = json.loads(submission.judge_result)
        assert submission.status == "FINISHED"
        assert submission.verdict == "IE"
        assert result["verdict"] == "IE"
        assert result["summary"] == (
            "The judge infrastructure was unavailable. Please try again later."
        )
        assert "daemon unavailable" not in submission.judge_result


def test_worker_startup_recovers_interrupted_work_without_requeueing():
    user_id, problem_id = _user_and_problem()
    with SessionLocal() as db:
        rows = [
            Submission(
                user_id=user_id,
                problem_id=problem_id,
                source_code="int main(){}",
                status=status,
            )
            for status in ("QUEUED", "COMPILING", "RUNNING")
        ]
        finished = Submission(
            user_id=user_id,
            problem_id=problem_id,
            source_code="int main(){}",
            status="FINISHED",
            verdict="AC",
        )
        build = BuildModel(
            problem_id=problem_id,
            created_by=user_id,
            kind="input",
            testcase_type="hidden",
            standard_source="int main(){}",
            standard_sha256="0" * 64,
            input_data="",
            status="RUNNING",
        )
        db.add_all([*rows, finished, build])
        custom_run = CustomRun(
            user_id=user_id,
            source_code="int main(){}",
            status="RUNNING",
        )
        db.add(custom_run)
        db.commit()
        ids = [row.id for row in rows]
        finished_id = finished.id
        build_id = build.id
        custom_run_id = custom_run.id

    assert recover_interrupted_work() == (2, 1)

    with SessionLocal() as db:
        queued, compiling, running = [db.get(Submission, row_id) for row_id in ids]
        assert queued.status == SubmissionStatus.QUEUED.value
        for row in (compiling, running):
            assert row.status == SubmissionStatus.FINISHED.value
            assert row.verdict == "IE"
            result = json.loads(row.judge_result)
            assert result["summary"] == INTERRUPTED_SUBMISSION_SUMMARY
            assert result["resources"]["memory_kb"] is None
        assert db.get(Submission, finished_id).verdict == "AC"
        recovered_build = db.get(BuildModel, build_id)
        assert recovered_build.status == BuildStatus.FAILED.value
        assert recovered_build.error == INTERRUPTED_BUILD_ERROR
        recovered_run = db.get(CustomRun, custom_run_id)
        assert recovered_run.status == "FAILED"
        assert "interrupted" in recovered_run.error


def test_worker_claims_and_finishes_custom_run(monkeypatch):
    user_id, _problem_id = _user_and_problem()
    with SessionLocal() as db:
        job = CustomRun(user_id=user_id, source_code="source", stdin="input")
        db.add(job)
        db.commit()
        job_id = job.id

    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.custom_run",
        lambda _judge, source, stdin: {
            "status": "OK",
            "exit_code": 0,
            "stdout": f"{source}:{stdin}",
            "stderr": "",
            "time_ms": 1,
            "memory_kb": None,
        },
    )
    assert claim_next_custom_run() == job_id
    assert claim_next_custom_run() is None
    process_custom_run(job_id)

    with SessionLocal() as db:
        job = db.get(CustomRun, job_id)
        assert job.status == "FINISHED"
        assert json.loads(job.result)["stdout"] == "source:input"


def test_claim_and_final_result_are_not_written_twice(monkeypatch):
    user_id, problem_id = _user_and_problem()
    with SessionLocal() as db:
        problem = db.get(Problem, problem_id)
        add_testcase(db, problem, "hidden", "", "1\n")
        submission = Submission(
            user_id=user_id,
            problem_id=problem_id,
            source_code="int main(){}",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    assert claim_next_submission() == submission_id
    assert claim_next_submission() is None
    calls = 0

    def judge(*_args, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_compiled"]()
        return (
            {
                "success": True,
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "time_ms": 1,
                "memory_kb": None,
                "timed_out": False,
                "output_exceeded": False,
                "oom_killed": False,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "output_truncated": False,
            },
            {
                "verdict": "AC",
                "summary": "Accepted.",
                "tests": {"total": 1, "passed": 1, "failed_test": None},
                "test_results": [],
                "resources": {"time_ms": 1, "memory_kb": None},
            },
        )

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", judge)
    judge_submission(submission_id)
    judge_submission(submission_id)
    assert calls == 1
    with SessionLocal() as db:
        row = db.get(Submission, submission_id)
        assert row.status == SubmissionStatus.FINISHED.value
        assert row.verdict == "AC"


def test_worker_lock_rejects_a_second_worker():
    with (
        exclusive_worker(),
        pytest.raises(InfrastructureError, match="Another MiniOJ worker"),
        exclusive_worker(),
    ):
        pytest.fail("second worker acquired the lock")


def test_stale_worker_job_directories_are_removed():
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    stale_judge = settings.jobs_dir / "judge-stale"
    stale_build = settings.jobs_dir / "testcase-build-stale"
    stale_run = settings.jobs_dir / "run-stale"
    stale_checker = settings.jobs_dir / "checker-stale"
    unrelated = settings.jobs_dir / "other-active"
    for path in (stale_judge, stale_build, stale_run, stale_checker, unrelated):
        path.mkdir(exist_ok=True)
    assert cleanup_stale_job_directories() == 4
    assert not stale_judge.exists()
    assert not stale_build.exists()
    assert not stale_run.exists()
    assert not stale_checker.exists()
    assert unrelated.exists()
    unrelated.rmdir()
