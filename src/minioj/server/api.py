from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.database import SessionLocal, get_db
from minioj.feedback import submission_feedback
from minioj.judge import CustomRunStatus, SubmissionStatus
from minioj.models import ApiToken, CustomRun, Problem, Submission, TestCase, User
from minioj.problems import (
    add_testcase,
    delete_problem,
    delete_testcase,
    ensure_problem_mutable,
    update_problem,
    update_testcase,
)
from minioj.schemas import (
    ProblemCreate,
    RegisterRequest,
    RunRequest,
    SubmissionCreate,
    TestCaseCreate,
    TokenCreateRequest,
)
from minioj.security import (
    PROBLEM_ID_RE,
    USERNAME_RE,
    create_api_token,
    hash_password,
    is_reserved_username,
    mask_token,
    valid_email,
    validate_password,
)
from minioj.server.dependencies import (
    admin_user,
    bearer_user,
    current_user,
    require_session_csrf,
)

router = APIRouter(prefix="/api/v1", tags=["api"])
logger = logging.getLogger("minioj.api")
submission_queue_lock = threading.Lock()
custom_run_queue_lock = threading.Lock()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _problem_summary(problem: Problem) -> dict:
    return {
        "problem_id": problem.id,
        "title": problem.title,
        "source": problem.source,
        "source_id": problem.source_id,
        "rating": problem.rating,
        "tags": [tag.strip() for tag in problem.tags.split(",") if tag.strip()],
        "limits": {
            "time_ms": problem.time_limit_ms,
            "memory_mb": problem.memory_limit_mb,
        },
    }


def _problem_detail(problem: Problem, *, agent: bool = False) -> dict:
    data = {
        "problem_id": problem.id,
        "title": problem.title,
        "statement": problem.statement,
        "input_specification": problem.input_specification,
        "output_specification": problem.output_specification,
        "notes": problem.notes,
        "limits": {
            "time_ms": problem.time_limit_ms,
            "memory_mb": problem.memory_limit_mb,
        },
        "samples": [
            {"input": sample.input, "output": sample.output}
            for sample in problem.samples
        ],
    }
    if not agent:
        data.update(
            {
                "source": problem.source,
                "source_id": problem.source_id,
                "source_url": problem.source_url,
                "rating": problem.rating,
                "tags": [tag.strip() for tag in problem.tags.split(",") if tag.strip()],
            }
        )
    return data


def _submission_detail(submission: Submission) -> dict:
    judge = json.loads(submission.judge_result) if submission.judge_result else {}
    return {
        "submission_id": submission.id,
        "problem_id": submission.problem_id,
        "language": submission.language,
        "status": submission.status,
        "verdict": submission.verdict,
        "tests": judge.get("tests"),
        "resources": judge.get("resources"),
        "created_at": _iso(submission.created_at),
        "started_at": _iso(submission.started_at),
        "finished_at": _iso(submission.finished_at),
    }


def _testcase_detail(testcase: TestCase) -> dict:
    return {
        "id": testcase.id,
        "type": testcase.type,
        "order": testcase.order,
        "input_sha256": testcase.input_sha256,
        "output_sha256": testcase.output_sha256,
        "created_at": _iso(testcase.created_at),
    }


def _require_problem_mutable(db: Session, problem_id: str) -> None:
    try:
        ensure_problem_mutable(db, problem_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def api_register(payload: RegisterRequest, db: Session = Depends(get_db)) -> dict:
    username = payload.username.strip()
    email = payload.email.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise HTTPException(
            status_code=422,
            detail="Username must contain 3-10 English letters (A-Z or a-z)",
        )
    if is_reserved_username(username):
        raise HTTPException(
            status_code=422,
            detail="This username is reserved for an administrator. Please choose another.",
        )
    if not valid_email(email):
        raise HTTPException(status_code=422, detail="A valid email is required")
    if payload.password != payload.password_confirmation:
        raise HTTPException(status_code=422, detail="Passwords do not match")
    password_error = validate_password(payload.password)
    if password_error:
        raise HTTPException(status_code=422, detail=password_error)
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(payload.password),
        role="user",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Username or email already exists")
    db.refresh(user)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
    }


@router.get("/me")
def api_me(user: User = Depends(current_user)) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": _iso(user.created_at),
    }


@router.get("/tokens")
def list_tokens(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    return [
        {
            "id": token.id,
            "name": token.name,
            "token_preview": token.token_preview,
            "created_at": _iso(token.created_at),
            "last_used_at": _iso(token.last_used_at),
            "expires_at": _iso(token.expires_at),
            "revoked_at": _iso(token.revoked_at),
        }
        for token in tokens
    ]


@router.post("/tokens", status_code=status.HTTP_201_CREATED)
def create_token(
    payload: TokenCreateRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    token_id, raw, digest, expires_at = create_api_token(payload.expires_in_days)
    token = ApiToken(
        id=token_id,
        user_id=user.id,
        name=payload.name.strip(),
        token_hash=digest,
        token_preview=mask_token(raw),
        expires_at=expires_at,
    )
    db.add(token)
    db.commit()
    return {
        "id": token.id,
        "name": token.name,
        "token": raw,
        "token_preview": token.token_preview,
        "expires_at": _iso(expires_at),
    }


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_token(
    token_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    require_session_csrf(request, authorization)
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status_code=404, detail="Token not found")
    db.delete(token)
    db.commit()
    return Response(status_code=204)


@router.get("/problems")
def list_problems(db: Session = Depends(get_db)) -> list[dict]:
    problems = db.scalars(
        select(Problem)
        .where(Problem.deleted_at.is_(None))
        .order_by(Problem.created_at.desc())
    ).all()
    return [_problem_summary(problem) for problem in problems]


@router.get("/problems/{problem_id}")
def get_problem(problem_id: str, db: Session = Depends(get_db)) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return _problem_detail(problem)


@router.get("/agent/problems/{problem_id}")
def get_agent_problem(
    problem_id: str, _user: User = Depends(bearer_user), db: Session = Depends(get_db)
) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return _problem_detail(problem, agent=True)


@router.post("/submissions", status_code=status.HTTP_202_ACCEPTED)
def create_submission(
    payload: SubmissionCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    if payload.language != "cpp20":
        raise HTTPException(status_code=422, detail="Only cpp20 is supported")
    if len(payload.source_code.encode()) > settings.source_limit_bytes:
        raise HTTPException(status_code=413, detail="Source code is too large")
    try:
        ensure_problem_mutable(db, payload.problem_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Problem not found") from exc
    problem = db.get(Problem, payload.problem_id)
    with submission_queue_lock:
        queued = db.scalar(
            select(func.count(Submission.id)).where(
                Submission.status == SubmissionStatus.QUEUED.value
            )
        )
        if queued >= settings.max_queued_submissions:
            raise HTTPException(
                status_code=429,
                detail="The submission queue is full. Please try again later.",
                headers={"Retry-After": str(settings.overload_retry_after_seconds)},
            )
        submission = Submission(
            user_id=user.id,
            problem_id=payload.problem_id,
            problem_revision=problem.revision,
            language=payload.language,
            source_code=payload.source_code,
            status=SubmissionStatus.QUEUED.value,
        )
        db.add(submission)
        db.commit()
    logger.info(
        "Created submission %s for user=%s problem=%s",
        submission.id,
        user.id,
        problem.id,
    )
    return {"submission_id": submission.id, "status": submission.status}


@router.get("/submissions/{submission_id}")
def get_submission(
    submission_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None or (submission.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Submission not found")
    return _submission_detail(submission)


@router.get("/agent/submissions/{submission_id}/feedback")
def get_agent_feedback(
    submission_id: int,
    user: User = Depends(bearer_user),
    db: Session = Depends(get_db),
) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None or (submission.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission_feedback(submission, settings.feedback_policy)


def _queue_custom_run(user_id: int, payload: RunRequest, db: Session) -> int:
    with custom_run_queue_lock:
        queued = db.scalar(
            select(func.count(CustomRun.id)).where(
                CustomRun.status == CustomRunStatus.QUEUED.value
            )
        )
        if queued >= settings.max_queued_runs:
            raise HTTPException(
                status_code=429,
                detail="The Custom Run queue is full. Please try again later.",
                headers={"Retry-After": str(settings.overload_retry_after_seconds)},
            )
        job = CustomRun(
            user_id=user_id,
            language=payload.language,
            source_code=payload.source_code,
            stdin=payload.stdin,
        )
        db.add(job)
        db.commit()
        job_id = job.id
    logger.info("Queued Custom Run %s for user=%s", job_id, user_id)
    return job_id


async def _wait_for_custom_run(job_id: int) -> dict:
    try:
        deadline = time.monotonic() + settings.custom_run_wait_seconds
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                job = db.get(CustomRun, job_id)
                if job is None:
                    break
                if job.status == CustomRunStatus.FINISHED.value and job.result:
                    result = json.loads(job.result)
                    db.delete(job)
                    db.commit()
                    return result
                if job.status in {
                    CustomRunStatus.FAILED.value,
                    CustomRunStatus.CANCELLED.value,
                }:
                    db.delete(job)
                    db.commit()
                    raise HTTPException(
                        status_code=503,
                        detail={
                            "verdict": "IE",
                            "summary": "The judge infrastructure was unavailable.",
                        },
                    )
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        _cancel_unclaimed_custom_run(job_id, "The requesting client disconnected.")
        raise

    _cancel_unclaimed_custom_run(
        job_id, "The request stopped waiting before a Worker claimed the job."
    )
    raise HTTPException(
        status_code=503,
        detail={
            "verdict": "IE",
            "summary": "Custom Run timed out waiting for the judge Worker.",
        },
    )


def _cancel_unclaimed_custom_run(job_id: int, error: str) -> None:
    """Cancel a queued job without taking ownership from a running Worker."""

    with SessionLocal() as db:
        db.execute(
            update(CustomRun)
            .where(
                CustomRun.id == job_id,
                CustomRun.status == CustomRunStatus.QUEUED.value,
            )
            .values(
                status=CustomRunStatus.CANCELLED.value,
                error=error,
                finished_at=datetime.now(UTC),
            )
        )
        db.commit()


@router.post("/runs")
async def custom_run(
    payload: RunRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    if payload.language != "cpp20":
        raise HTTPException(status_code=422, detail="Only cpp20 is supported")
    if len(payload.source_code.encode()) > settings.source_limit_bytes:
        raise HTTPException(status_code=413, detail="Source code is too large")
    if len(payload.stdin.encode()) > settings.stdin_limit_bytes:
        raise HTTPException(status_code=413, detail="Input is too large")
    job_id = _queue_custom_run(user.id, payload, db)
    return await _wait_for_custom_run(job_id)


@router.post("/admin/problems", status_code=status.HTTP_201_CREATED)
def admin_create_problem(
    payload: ProblemCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    if not PROBLEM_ID_RE.fullmatch(payload.id):
        raise HTTPException(
            status_code=422,
            detail=(
                "Problem id must be 3-80 characters using letters, numbers, or "
                "hyphens, and must start and end with a letter or number"
            ),
        )
    if db.get(Problem, payload.id):
        raise HTTPException(status_code=409, detail="Problem id already exists")
    values = payload.model_dump()
    problem = Problem(**values, created_by=admin.id)
    db.add(problem)
    db.commit()
    return _problem_detail(problem)


@router.put("/admin/problems/{problem_id}")
def admin_update_problem(
    problem_id: str,
    payload: ProblemCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    if payload.id != problem_id:
        raise HTTPException(status_code=422, detail="Problem id cannot be changed")
    _require_problem_mutable(db, problem_id)
    update_problem(db, problem, payload.model_dump(exclude={"id"}))
    return _problem_detail(problem)


@router.delete("/admin/problems/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_problem(
    problem_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    require_session_csrf(request, authorization)
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        delete_problem(db, problem)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=204)


@router.post(
    "/admin/problems/{problem_id}/testcases", status_code=status.HTTP_201_CREATED
)
def admin_add_testcase(
    problem_id: str,
    payload: TestCaseCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    _require_problem_mutable(db, problem_id)
    try:
        testcase = add_testcase(
            db,
            problem,
            payload.type,
            payload.input,
            payload.output,
            input_sha256=payload.input_sha256,
            output_sha256=payload.output_sha256,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _testcase_detail(testcase)


@router.put("/admin/problems/{problem_id}/testcases/{testcase_id}")
def admin_update_testcase(
    problem_id: str,
    testcase_id: int,
    payload: TestCaseCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    _require_problem_mutable(db, problem_id)
    try:
        testcase = update_testcase(
            db,
            problem,
            testcase_id,
            payload.type,
            input_data=payload.input,
            output_data=payload.output,
            input_sha256=payload.input_sha256,
            output_sha256=payload.output_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _testcase_detail(testcase)


@router.delete(
    "/admin/problems/{problem_id}/testcases/{testcase_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def admin_delete_testcase(
    problem_id: str,
    testcase_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    require_session_csrf(request, authorization)
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    _require_problem_mutable(db, problem_id)
    try:
        delete_testcase(db, problem, testcase_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)
