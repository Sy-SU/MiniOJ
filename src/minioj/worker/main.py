from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from datetime import UTC, datetime

from sqlalchemy import select, update

from minioj.config import settings
from minioj.database import SessionLocal, init_db
from minioj.judge import DockerJudge, InfrastructureError, TestcaseBuildError
from minioj.models import Problem, Submission, TestCase, TestcaseBuild
from minioj.problems import (
    add_testcase_batch,
    ensure_problem_mutable,
    testcase_contents,
)

logger = logging.getLogger("minioj.worker")
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def claim_next_submission() -> int | None:
    with SessionLocal() as db:
        submission_id = db.scalar(
            select(Submission.id)
            .where(Submission.status == "QUEUED")
            .order_by(Submission.created_at)
            .limit(1)
        )
        if submission_id is None:
            return None
        claimed = db.execute(
            update(Submission)
            .where(Submission.id == submission_id, Submission.status == "QUEUED")
            .values(status="COMPILING", started_at=datetime.now(UTC))
        )
        db.commit()
        return submission_id if claimed.rowcount == 1 else None


def claim_next_testcase_build() -> int | None:
    with SessionLocal() as db:
        build_id = db.scalar(
            select(TestcaseBuild.id)
            .where(TestcaseBuild.status == "QUEUED")
            .order_by(TestcaseBuild.created_at)
            .limit(1)
        )
        if build_id is None:
            return None
        claimed = db.execute(
            update(TestcaseBuild)
            .where(TestcaseBuild.id == build_id, TestcaseBuild.status == "QUEUED")
            .values(status="RUNNING", started_at=datetime.now(UTC))
        )
        db.commit()
        return build_id if claimed.rowcount == 1 else None


def finish_testcase_build_failed(build_id: int, error: str) -> None:
    logger.error("Testcase build %s: %s", build_id, error)
    with SessionLocal() as db:
        build = db.get(TestcaseBuild, build_id)
        if build and build.status == "RUNNING":
            build.status = "FAILED"
            build.error = error[:4000]
            build.finished_at = datetime.now(UTC)
            db.commit()


def process_testcase_build(build_id: int) -> None:
    with SessionLocal() as db:
        build = db.get(TestcaseBuild, build_id)
        if build is None or build.status != "RUNNING":
            return
        standard_source = build.standard_source
        input_data = build.input_data
        generator_source = build.generator_source
        case_count = build.case_count
        base_seed = build.base_seed
    try:
        with SessionLocal() as db:
            build = db.get(TestcaseBuild, build_id)
            if build is None or build.status != "RUNNING":
                return
            ensure_problem_mutable(db, build.problem_id)
            if (
                build.standard_sha256
                != db.get(Problem, build.problem_id).standard_sha256
            ):
                raise ValueError(
                    "Standard solution has been modified; queue a new testcase build."
                )
        cases = DockerJudge().build_testcases(
            standard_source,
            input_data=input_data,
            generator_source=generator_source,
            case_count=case_count,
            base_seed=base_seed,
        )
        with SessionLocal() as db:
            build = db.get(TestcaseBuild, build_id)
            if build is None or build.status != "RUNNING":
                return
            problem = db.get(Problem, build.problem_id)
            if problem is None:
                raise ValueError("Problem no longer exists.")
            ensure_problem_mutable(db, problem.id)
            db.refresh(build)
            if build.status != "RUNNING":
                return
            if build.standard_sha256 != problem.standard_sha256:
                raise ValueError(
                    "Standard solution has been modified; queue a new testcase build."
                )

            def finish(testcases: list[TestCase]) -> None:
                build.status = "FINISHED"
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


def finish_with_ie(submission_id: int, summary: str) -> None:
    logger.error("Submission %s: %s", submission_id, summary)
    result = {
        "verdict": "IE",
        "summary": summary,
        "tests": {"total": 0, "passed": 0, "failed_test": None},
        "resources": {"time_ms": 0, "memory_kb": 0},
    }
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        if submission and submission.status != "FINISHED":
            submission.status = "FINISHED"
            submission.verdict = "IE"
            submission.judge_result = json.dumps(result)
            submission.finished_at = datetime.now(UTC)
            db.commit()


def judge_submission(submission_id: int) -> None:
    try:
        with SessionLocal() as db:
            submission = db.get(Submission, submission_id)
            if submission is None or submission.status != "COMPILING":
                return
            ensure_problem_mutable(db, submission.problem_id)
            db.refresh(submission)
            if submission.status != "COMPILING":
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
                row = db.get(Submission, submission_id)
                if row and row.status == "COMPILING":
                    row.status = "RUNNING"
                    db.commit()

        compile_result, judge_result = DockerJudge().judge(
            source_code, tests, time_limit_ms, memory_limit_mb, on_compiled=mark_running
        )
    except (InfrastructureError, OSError, ValueError) as exc:
        finish_with_ie(submission_id, f"Judge infrastructure error: {exc}")
        return
    except Exception:
        logger.exception("Unexpected error while judging %s", submission_id)
        finish_with_ie(submission_id, "Unexpected judge infrastructure error.")
        return
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        if submission and submission.status != "FINISHED":
            submission.compile_result = json.dumps(compile_result)
            submission.judge_result = json.dumps(judge_result)
            submission.verdict = judge_result["verdict"]
            submission.status = "FINISHED"
            submission.finished_at = datetime.now(UTC)
            db.commit()
    logger.info("Submission %s finished: %s", submission_id, judge_result["verdict"])


def run_worker(poll_interval: float = 1.0, once: bool = False) -> None:
    settings.validate_worker()
    logger.info("Starting worker: initializing database")
    init_db()
    judge = DockerJudge()
    logger.info("Checking Docker judge image: %s", judge.image)
    judge.ensure_available()
    logger.info(
        "Worker ready (poll interval: %s seconds). Press Ctrl+C to stop.", poll_interval
    )
    waiting = False
    while running:
        did_work = False
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
        if did_work:
            continue
        if once:
            logger.info("No queued work; worker exiting (--once).")
            return
        if not waiting:
            logger.info("No queued work; waiting for testcase builds or submissions.")
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
