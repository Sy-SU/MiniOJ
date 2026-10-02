from __future__ import annotations

import hashlib
import json
import logging
import re

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.models import Problem, Submission, SubmissionJudgeRun, User, utcnow
from minioj.permissions import Permission, require_permission
from minioj.problems import ensure_problem_mutable

logger = logging.getLogger("minioj.submissions")


class IdempotencyConflict(HTTPException):
    error_code = "idempotency_conflict"

    def __init__(self):
        super().__init__(
            409, "Idempotency key was already used for a different request"
        )


SNAPSHOT_FIELDS = (
    "problem_revision",
    "status",
    "verdict",
    "compile_result",
    "judge_result",
    "started_at",
    "finished_at",
)


def sync_judge_run(
    db: Session, submission_id: int, generation: int | None = None
) -> None:
    """Snapshot the current generation in the same transaction as its state change."""
    submission = db.get(Submission, submission_id)
    if submission is None:
        return
    db.refresh(submission)
    if generation is not None and generation != submission.judge_generation:
        return
    db.execute(
        update(SubmissionJudgeRun)
        .where(
            SubmissionJudgeRun.submission_id == submission.id,
            SubmissionJudgeRun.generation == submission.judge_generation,
        )
        .values(**{field: getattr(submission, field) for field in SNAPSHOT_FIELDS})
    )


def check_queue_capacity(db: Session, *, limits=None) -> None:
    limits = limits or settings
    count = db.scalar(
        select(func.count(Submission.id)).where(Submission.status == "QUEUED")
    )
    if count >= limits.max_queued_submissions:
        raise HTTPException(
            status_code=429,
            detail="The submission queue is full. Please try again later.",
            headers={"Retry-After": str(limits.overload_retry_after_seconds)},
        )


def enqueue_submission(
    db: Session,
    user: User,
    payload,
    *,
    contest_id: int | None = None,
    limits=None,
    created_at=None,
    idempotency_key: str | None = None,
) -> Submission:
    limits = limits or settings
    if len(payload.source_code.encode("utf-8")) > limits.source_limit_bytes:
        raise HTTPException(status_code=413, detail="Source code is too large")
    key_hash = payload_hash = None
    if idempotency_key is not None:
        if not re.fullmatch(r"[\x21-\x7e]{1,128}", idempotency_key):
            raise HTTPException(
                422,
                "Idempotency-Key must be 1-128 printable ASCII characters without spaces",
            )
        key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
        payload_hash = hashlib.sha256(
            json.dumps(
                {
                    "problem_id": payload.problem_id,
                    "language": payload.language,
                    "source_code": payload.source_code,
                    "contest_id": contest_id,
                },
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        # Lock before looking up, including replays after problem deletion or queue fill.
        # SQLite serializes writers across connections/processes, not only the API mutex.
        db.execute(
            update(User).where(User.id == user.id).values(is_active=User.is_active)
        )
        existing = db.scalar(
            select(Submission).where(
                Submission.user_id == user.id,
                Submission.idempotency_key_hash == key_hash,
            )
        )
        if existing is not None:
            if existing.request_payload_sha256 != payload_hash:
                raise IdempotencyConflict()
            db.commit()
            return existing
    try:
        ensure_problem_mutable(db, payload.problem_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Problem not found") from exc
    # ensure_problem_mutable holds the SQLite write lock across capacity + insert.
    check_queue_capacity(db, limits=limits)
    problem = db.get(Problem, payload.problem_id)
    submission = Submission(
        user_id=user.id,
        problem_id=problem.id,
        problem_revision=problem.revision,
        source_code=payload.source_code,
        language=payload.language,
        contest_id=contest_id,
        created_at=created_at or utcnow(),
        status="QUEUED",
        idempotency_key_hash=key_hash,
        request_payload_sha256=payload_hash,
    )
    db.add(submission)
    db.commit()
    logger.info(
        "Created submission %s user=%s problem=%s contest=%s",
        submission.id,
        user.id,
        problem.id,
        contest_id,
    )
    return submission


def rejudge_submission(db: Session, submission_id: int, actor: User) -> Submission:
    require_permission(actor, Permission.REJUDGE)
    # Acquire a write lock before reading the current generation or taking a snapshot.
    locked = db.execute(
        update(Submission)
        .where(Submission.id == submission_id)
        .values(judge_generation=Submission.judge_generation)
    )
    if locked.rowcount != 1:
        raise HTTPException(status_code=404, detail="Submission not found")
    submission = db.get(Submission, submission_id)
    db.refresh(submission)
    if submission.status != "FINISHED":
        raise HTTPException(
            status_code=409, detail="Submission is already queued or judging"
        )
    try:
        ensure_problem_mutable(db, submission.problem_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail="Cannot rejudge a deleted problem"
        ) from exc
    check_queue_capacity(db)
    sync_judge_run(db, submission_id)
    generation = submission.judge_generation + 1
    revision = db.get(Problem, submission.problem_id).revision
    changed = db.execute(
        update(Submission)
        .where(
            Submission.id == submission_id,
            Submission.status == "FINISHED",
            Submission.judge_generation == generation - 1,
        )
        .values(
            judge_generation=generation,
            problem_revision=revision,
            status="QUEUED",
            verdict=None,
            compile_result=None,
            judge_result=None,
            started_at=None,
            finished_at=None,
        )
    )
    if changed.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Submission has changed; retry after judging finishes",
        )
    db.add(
        SubmissionJudgeRun(
            submission_id=submission_id,
            generation=generation,
            problem_revision=revision,
            trigger_type="rejudge",
            triggered_by=actor.id,
            status="QUEUED",
            created_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(submission)
    logger.info(
        "Rejudge submission=%s generation=%s actor=%s",
        submission_id,
        generation,
        actor.id,
    )
    return submission
