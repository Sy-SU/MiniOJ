from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from datetime import UTC, datetime

from sqlalchemy import select, update

from minioj.database import SessionLocal, init_db
from minioj.judge import DockerJudge, InfrastructureError
from minioj.models import Problem, Submission, TestCase
from minioj.problems import testcase_contents

logger = logging.getLogger("minioj.worker")
running = True


def _stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def claim_next_submission() -> str | None:
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


def finish_with_ie(submission_id: str, summary: str) -> None:
    logger.error("Submission %s: %s", submission_id, summary)
    result = {
        "verdict": "IE",
        "summary": summary,
        "tests": {"total": 0, "passed": 0, "failed_test": None},
        "resources": {"time_ms": 0, "memory_kb": 0},
    }
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        if submission:
            submission.status = "FINISHED"
            submission.verdict = "IE"
            submission.judge_result = json.dumps(result)
            submission.finished_at = datetime.now(UTC)
            db.commit()


def judge_submission(submission_id: str) -> None:
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        if submission is None:
            return
        problem = db.get(Problem, submission.problem_id)
        if problem is None:
            finish_with_ie(submission_id, "Problem no longer exists.")
            return
        testcase_rows = db.scalars(
            select(TestCase)
            .where(TestCase.problem_id == problem.id)
            .order_by(TestCase.order)
        ).all()
        source_code = submission.source_code
        time_limit_ms = problem.time_limit_ms
        memory_limit_mb = problem.memory_limit_mb
    if not testcase_rows:
        finish_with_ie(submission_id, "Problem has no testcases.")
        return
    try:
        tests = [testcase_contents(testcase) for testcase in testcase_rows]

        def mark_running() -> None:
            with SessionLocal() as db:
                row = db.get(Submission, submission_id)
                if row:
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
        if submission:
            submission.compile_result = json.dumps(compile_result)
            submission.judge_result = json.dumps(judge_result)
            submission.verdict = judge_result["verdict"]
            submission.status = "FINISHED"
            submission.finished_at = datetime.now(UTC)
            db.commit()
    logger.info("Submission %s finished: %s", submission_id, judge_result["verdict"])


def run_worker(poll_interval: float = 1.0, once: bool = False) -> None:
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
        submission_id = claim_next_submission()
        if submission_id:
            waiting = False
            logger.info("Judging submission %s", submission_id)
            judge_submission(submission_id)
        elif once:
            logger.info("No queued submissions; worker exiting (--once).")
            return
        else:
            if not waiting:
                logger.info("No queued submissions; waiting for new submissions.")
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
