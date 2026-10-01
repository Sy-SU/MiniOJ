from __future__ import annotations

import argparse
import fcntl
import json
import logging
import shutil
import signal
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import IO

from sqlalchemy import delete, select, update

from minioj.config import settings
from minioj.database import SessionLocal, init_db
from minioj.judge import (
    CustomRunStatus,
    DockerJudge,
    InfrastructureError,
    SubmissionStatus,
    TestcaseBuildError,
    TestcaseBuildStatus,
    Verdict,
    validate_submission_result,
)
from minioj.models import CustomRun, Problem, Submission, TestCase, TestcaseBuild
from minioj.problems import (
    add_testcase_batch,
    ensure_problem_mutable,
    testcase_contents,
)

logger = logging.getLogger("minioj.worker")
running = True

INTERRUPTED_SUBMISSION_SUMMARY = (
    "Judging was interrupted before completion. Please submit again."
)
INTERRUPTED_BUILD_ERROR = "Testcase construction was interrupted. Queue a new build."
INTERRUPTED_CUSTOM_RUN_ERROR = "Custom Run was interrupted. Please run the code again."


@contextmanager
def exclusive_worker() -> Iterator[IO[str]]:
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    lock_path = settings.jobs_dir / ".worker.lock"
    lock_file = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InfrastructureError(
                "Another MiniOJ worker is already using this job directory."
            ) from exc
        yield lock_file
    finally:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        finally:
            lock_file.close()


def cleanup_stale_job_directories() -> int:
    removed = 0
    for prefix in ("judge-", "testcase-build-", "run-"):
        for path in settings.jobs_dir.glob(f"{prefix}*"):
            if not path.is_dir() or path.is_symlink():
                continue
            try:
                shutil.rmtree(path)
            except OSError as exc:
                logger.exception("Could not remove stale job directory %s", path)
                raise InfrastructureError(
                    "Could not remove a stale judge job directory."
                ) from exc
            removed += 1
    if removed:
        logger.warning("Removed %s stale judge job directory/directories", removed)
    return removed


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def claim_next_submission() -> int | None:
    with SessionLocal() as db:
        submission_id = db.scalar(
            select(Submission.id)
            .where(Submission.status == SubmissionStatus.QUEUED.value)
            .order_by(Submission.created_at)
            .limit(1)
        )
        if submission_id is None:
            return None
        claimed = db.execute(
            update(Submission)
            .where(
                Submission.id == submission_id,
                Submission.status == SubmissionStatus.QUEUED.value,
            )
            .values(
                status=SubmissionStatus.COMPILING.value,
                started_at=datetime.now(UTC),
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return None
        logger.info("Claimed submission %s", submission_id)
        return submission_id


def claim_next_custom_run() -> int | None:
    with SessionLocal() as db:
        run_id = db.scalar(
            select(CustomRun.id)
            .where(CustomRun.status == CustomRunStatus.QUEUED.value)
            .order_by(CustomRun.created_at)
            .limit(1)
        )
        if run_id is None:
            return None
        claimed = db.execute(
            update(CustomRun)
            .where(
                CustomRun.id == run_id,
                CustomRun.status == CustomRunStatus.QUEUED.value,
            )
            .values(
                status=CustomRunStatus.RUNNING.value,
                started_at=datetime.now(UTC),
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return None
        logger.info("Claimed Custom Run %s", run_id)
        return run_id


def process_custom_run(run_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(CustomRun, run_id)
        if job is None or job.status != CustomRunStatus.RUNNING.value:
            return
        source_code = job.source_code
        stdin = job.stdin
    try:
        result = DockerJudge(owner=settings.worker_owner).custom_run(source_code, stdin)
    except (InfrastructureError, OSError) as exc:
        logger.error("Custom Run %s infrastructure error: %s", run_id, exc)
        with SessionLocal() as db:
            db.execute(
                update(CustomRun)
                .where(
                    CustomRun.id == run_id,
                    CustomRun.status == CustomRunStatus.RUNNING.value,
                )
                .values(
                    status=CustomRunStatus.FAILED.value,
                    error="The judge infrastructure was unavailable.",
                    finished_at=datetime.now(UTC),
                )
            )
            db.commit()
        return
    except Exception:
        logger.exception("Unexpected error while processing Custom Run %s", run_id)
        with SessionLocal() as db:
            db.execute(
                update(CustomRun)
                .where(
                    CustomRun.id == run_id,
                    CustomRun.status == CustomRunStatus.RUNNING.value,
                )
                .values(
                    status=CustomRunStatus.FAILED.value,
                    error="The judge encountered an internal error.",
                    finished_at=datetime.now(UTC),
                )
            )
            db.commit()
        return
    with SessionLocal() as db:
        persisted = db.execute(
            update(CustomRun)
            .where(
                CustomRun.id == run_id,
                CustomRun.status == CustomRunStatus.RUNNING.value,
            )
            .values(
                status=CustomRunStatus.FINISHED.value,
                result=json.dumps(result),
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
    if persisted.rowcount == 1:
        logger.info("Custom Run %s finished: %s", run_id, result.get("status"))


def cleanup_expired_custom_runs() -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    with SessionLocal() as db:
        removed = db.execute(
            delete(CustomRun).where(
                CustomRun.status.in_(
                    [
                        CustomRunStatus.FINISHED.value,
                        CustomRunStatus.FAILED.value,
                        CustomRunStatus.CANCELLED.value,
                    ]
                ),
                CustomRun.finished_at < cutoff,
            )
        ).rowcount
        db.commit()
    if removed:
        logger.info("Removed %s expired Custom Run job(s)", removed)
    return removed


def claim_next_testcase_build() -> int | None:
    with SessionLocal() as db:
        build_id = db.scalar(
            select(TestcaseBuild.id)
            .where(TestcaseBuild.status == TestcaseBuildStatus.QUEUED.value)
            .order_by(TestcaseBuild.created_at)
            .limit(1)
        )
        if build_id is None:
            return None
        claimed = db.execute(
            update(TestcaseBuild)
            .where(
                TestcaseBuild.id == build_id,
                TestcaseBuild.status == TestcaseBuildStatus.QUEUED.value,
            )
            .values(
                status=TestcaseBuildStatus.RUNNING.value,
                started_at=datetime.now(UTC),
            )
        )
        db.commit()
        if claimed.rowcount != 1:
            return None
        logger.info("Claimed testcase build %s", build_id)
        return build_id


def _ie_result(summary: str) -> dict[str, object]:
    return {
        "verdict": Verdict.IE.value,
        "summary": summary,
        "tests": {"total": 0, "passed": 0, "failed_test": None},
        "test_results": [],
        "resources": {"time_ms": 0, "memory_kb": None},
    }


def recover_interrupted_work() -> tuple[int, int]:
    now = datetime.now(UTC)
    result = _ie_result(INTERRUPTED_SUBMISSION_SUMMARY)
    validate_submission_result(SubmissionStatus.FINISHED, Verdict.IE, None, result)
    with SessionLocal() as db:
        submissions = db.execute(
            update(Submission)
            .where(
                Submission.status.in_(
                    [
                        SubmissionStatus.COMPILING.value,
                        SubmissionStatus.RUNNING.value,
                    ]
                )
            )
            .values(
                status=SubmissionStatus.FINISHED.value,
                verdict=Verdict.IE.value,
                judge_result=json.dumps(result),
                finished_at=now,
            )
        ).rowcount
        builds = db.execute(
            update(TestcaseBuild)
            .where(TestcaseBuild.status == TestcaseBuildStatus.RUNNING.value)
            .values(
                status=TestcaseBuildStatus.FAILED.value,
                error=INTERRUPTED_BUILD_ERROR,
                finished_at=now,
            )
        ).rowcount
        custom_runs = db.execute(
            update(CustomRun)
            .where(CustomRun.status == CustomRunStatus.RUNNING.value)
            .values(
                status=CustomRunStatus.FAILED.value,
                error=INTERRUPTED_CUSTOM_RUN_ERROR,
                finished_at=now,
            )
        ).rowcount
        db.commit()
    if submissions or builds or custom_runs:
        logger.warning(
            "Recovered interrupted work: submissions=%s testcase_builds=%s custom_runs=%s",
            submissions,
            builds,
            custom_runs,
        )
    return submissions, builds


def finish_testcase_build_failed(build_id: int, error: str) -> None:
    logger.error("Testcase build %s: %s", build_id, error)
    with SessionLocal() as db:
        build = db.get(TestcaseBuild, build_id)
        if build and build.status == TestcaseBuildStatus.RUNNING.value:
            build.status = TestcaseBuildStatus.FAILED.value
            build.error = error[:4000]
            build.finished_at = datetime.now(UTC)
            db.commit()


def process_testcase_build(build_id: int) -> None:
    with SessionLocal() as db:
        build = db.get(TestcaseBuild, build_id)
        if build is None or build.status != TestcaseBuildStatus.RUNNING.value:
            return
        standard_source = build.standard_source
        input_data = build.input_data
        generator_source = build.generator_source
        case_count = build.case_count
        base_seed = build.base_seed
    try:
        with SessionLocal() as db:
            build = db.get(TestcaseBuild, build_id)
            if build is None or build.status != TestcaseBuildStatus.RUNNING.value:
                return
            ensure_problem_mutable(db, build.problem_id)
            if (
                build.standard_sha256
                != db.get(Problem, build.problem_id).standard_sha256
            ):
                raise ValueError(
                    "Standard solution has been modified; queue a new testcase build."
                )
        cases = DockerJudge(owner=settings.worker_owner).build_testcases(
            standard_source,
            input_data=input_data,
            generator_source=generator_source,
            case_count=case_count,
            base_seed=base_seed,
        )
        with SessionLocal() as db:
            build = db.get(TestcaseBuild, build_id)
            if build is None or build.status != TestcaseBuildStatus.RUNNING.value:
                return
            problem = db.get(Problem, build.problem_id)
            if problem is None:
                raise ValueError("Problem no longer exists.")
            ensure_problem_mutable(db, problem.id)
            db.refresh(build)
            if build.status != TestcaseBuildStatus.RUNNING.value:
                return
            if build.standard_sha256 != problem.standard_sha256:
                raise ValueError(
                    "Standard solution has been modified; queue a new testcase build."
                )

            def finish(testcases: list[TestCase]) -> None:
                build.status = TestcaseBuildStatus.FINISHED.value
                build.error = None
                build.created_count = len(testcases)
                build.finished_at = datetime.now(UTC)

            add_testcase_batch(
                db,
                problem,
                build.testcase_type,
                cases,
                finalize=finish,
            )
    except (InfrastructureError, TestcaseBuildError, OSError, ValueError) as exc:
        finish_testcase_build_failed(build_id, str(exc))
        return
    except Exception:
        logger.exception(
            "Unexpected error while building testcases for job %s", build_id
        )
        finish_testcase_build_failed(build_id, "Unexpected testcase build error.")
        return
    logger.info("Testcase build %s finished: %s case(s)", build_id, len(cases))


def finish_with_ie(
    submission_id: int, public_summary: str, *, internal_detail: str | None = None
) -> None:
    if internal_detail:
        logger.error(
            "Submission %s infrastructure error: %s", submission_id, internal_detail
        )
    else:
        logger.error("Submission %s: %s", submission_id, public_summary)
    result = _ie_result(public_summary)
    validate_submission_result(SubmissionStatus.FINISHED, Verdict.IE, None, result)
    with SessionLocal() as db:
        finished = db.execute(
            update(Submission)
            .where(
                Submission.id == submission_id,
                Submission.status != SubmissionStatus.FINISHED.value,
            )
            .values(
                status=SubmissionStatus.FINISHED.value,
                verdict=Verdict.IE.value,
                judge_result=json.dumps(result),
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
        if finished.rowcount != 1:
            logger.warning(
                "Submission %s was already terminal; IE result was not overwritten",
                submission_id,
            )


def judge_submission(submission_id: int) -> None:
    try:
        with SessionLocal() as db:
            submission = db.get(Submission, submission_id)
            if (
                submission is None
                or submission.status != SubmissionStatus.COMPILING.value
            ):
                return
            ensure_problem_mutable(db, submission.problem_id)
            db.refresh(submission)
            if submission.status != SubmissionStatus.COMPILING.value:
                return
            problem = db.get(Problem, submission.problem_id)
            if submission.problem_revision != problem.revision:
                raise ValueError(
                    "Problem has been modified before judging started. Please submit again."
                )
            testcase_rows = db.scalars(
                select(TestCase)
                .where(TestCase.problem_id == problem.id)
                .order_by(TestCase.order)
            ).all()
            if not testcase_rows:
                raise ValueError("Problem has no testcases.")
            source_code = submission.source_code
            time_limit_ms = problem.time_limit_ms
            memory_limit_mb = problem.memory_limit_mb
            # Snapshot all judge inputs while serialized with testcase writers.
            # Release the database lock before starting any containers.
            tests = [testcase_contents(testcase) for testcase in testcase_rows]

        def mark_running() -> None:
            with SessionLocal() as db:
                changed = db.execute(
                    update(Submission)
                    .where(
                        Submission.id == submission_id,
                        Submission.status == SubmissionStatus.COMPILING.value,
                    )
                    .values(status=SubmissionStatus.RUNNING.value)
                )
                db.commit()
                if changed.rowcount != 1:
                    raise InfrastructureError(
                        "Submission ownership was lost before execution."
                    )
            logger.info(
                "Submission %s compilation finished; running tests", submission_id
            )

        logger.info("Submission %s compilation started", submission_id)
        compile_result, judge_result = DockerJudge(owner=settings.worker_owner).judge(
            source_code, tests, time_limit_ms, memory_limit_mb, on_compiled=mark_running
        )
    except ValueError as exc:
        detail = str(exc)
        if "modified before judging" in detail:
            public_summary = (
                "The problem was modified before judging started. Please submit again."
            )
        elif "deleted" in detail:
            public_summary = (
                "The problem was deleted before judging started. Please submit again."
            )
        else:
            public_summary = "The problem's judge data is unavailable. Please contact an administrator."
        finish_with_ie(
            submission_id,
            public_summary,
            internal_detail=detail,
        )
        return
    except (InfrastructureError, OSError) as exc:
        finish_with_ie(
            submission_id,
            "The judge infrastructure was unavailable. Please try again later.",
            internal_detail=str(exc),
        )
        return
    except Exception:
        logger.exception("Unexpected error while judging %s", submission_id)
        finish_with_ie(
            submission_id,
            "The judge encountered an internal error. Please try again later.",
        )
        return
    verdict = Verdict(judge_result["verdict"])
    validate_submission_result(
        SubmissionStatus.FINISHED, verdict, compile_result, judge_result
    )
    with SessionLocal() as db:
        persisted = db.execute(
            update(Submission)
            .where(
                Submission.id == submission_id,
                Submission.status.in_(
                    [
                        SubmissionStatus.COMPILING.value,
                        SubmissionStatus.RUNNING.value,
                    ]
                ),
            )
            .values(
                compile_result=json.dumps(compile_result),
                judge_result=json.dumps(judge_result),
                verdict=verdict.value,
                status=SubmissionStatus.FINISHED.value,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()
    if persisted.rowcount != 1:
        logger.warning(
            "Submission %s was already terminal; duplicate judge result was discarded",
            submission_id,
        )
        return
    logger.info("Submission %s finished: %s", submission_id, verdict.value)


def run_worker(poll_interval: float = 1.0, once: bool = False) -> None:
    settings.validate_worker()
    with exclusive_worker():
        logger.info("Starting worker: initializing database")
        init_db()
        judge = DockerJudge(owner=settings.worker_owner)
        recover_interrupted_work()
        judge.cleanup_owned_containers()
        cleanup_stale_job_directories()
        cleanup_expired_custom_runs()
        logger.info("Checking Docker judge image: %s", judge.image)
        judge.ensure_available()
        logger.info(
            "Worker ready (poll interval: %s seconds). Press Ctrl+C to stop.",
            poll_interval,
        )
        waiting = False
        last_custom_run_cleanup = time.monotonic()
        while running:
            try:
                if time.monotonic() - last_custom_run_cleanup >= 60:
                    cleanup_expired_custom_runs()
                    last_custom_run_cleanup = time.monotonic()
                did_work = False
                custom_run_id = claim_next_custom_run()
                if custom_run_id:
                    did_work = True
                    waiting = False
                    logger.info("Processing Custom Run %s", custom_run_id)
                    process_custom_run(custom_run_id)
                build_id = claim_next_testcase_build()
                if build_id:
                    did_work = True
                    waiting = False
                    logger.info("Processing testcase build %s", build_id)
                    process_testcase_build(build_id)
                submission_id = claim_next_submission()
                if submission_id:
                    did_work = True
                    waiting = False
                    logger.info("Judging submission %s", submission_id)
                    judge_submission(submission_id)
            except Exception:
                logger.exception("Worker loop failed while claiming or processing work")
                try:
                    recover_interrupted_work()
                except Exception:
                    logger.exception(
                        "Worker could not recover interrupted database work"
                    )
                did_work = False
            if did_work:
                continue
            if once:
                logger.info("No queued work; worker exiting (--once).")
                return
            if not waiting:
                logger.info(
                    "No queued work; waiting for Custom Runs, testcase builds, or submissions."
                )
                waiting = True
            time.sleep(poll_interval)
        logger.info("Worker stopped.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the MiniOJ judge worker")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        run_worker(args.poll_interval, args.once)
    except InfrastructureError as exc:
        raise SystemExit(f"Worker cannot start: {exc}")


if __name__ == "__main__":
    main()
