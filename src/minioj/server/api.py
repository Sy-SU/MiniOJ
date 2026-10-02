from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.database import SessionLocal, get_db
from minioj.feedback import feedback_response, submission_feedback
from minioj.judge import CustomRunStatus
from minioj.judge.testlib import CheckerBundle
from minioj.models import ApiToken, CustomRun, Problem, Submission, TestCase, User
from minioj.permissions import Permission, can
from minioj.problems import (
    PROBLEM_PAGE_SIZE,
    ProblemSort,
    add_testcase,
    delete_problem,
    delete_testcase,
    ensure_problem_mutable,
    problem_list_query,
    update_problem,
    update_testcase,
)
from minioj.schemas import (
    AgentProblemResponse,
    CurrentUserResponse,
    CustomRunResponse,
    ErrorResponse,
    FeedbackResponse,
    ProblemCreate,
    ProblemDetailResponse,
    ProblemSummaryResponse,
    RegisterRequest,
    RunRequest,
    SubmissionCreate,
    SubmissionCreatedResponse,
    SubmissionDetailResponse,
    TestCaseCreate,
    TestCaseResponse,
    TokenCreatedResponse,
    TokenCreateRequest,
    TokenMetadataResponse,
    UserCreatedResponse,
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
from minioj.submissions import enqueue_submission, rejudge_submission

API_ERROR_RESPONSES = {
    code: {"model": ErrorResponse}
    for code in (400, 401, 403, 404, 405, 409, 413, 422, 429, 500, 503)
}
router = APIRouter(prefix="/api/v1", tags=["api"], responses=API_ERROR_RESPONSES)
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
                "checker": problem.checker,
            }
        )
    return data


def _submission_detail(submission: Submission) -> dict:
    judge = submission_feedback(submission, settings.feedback_policy)
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


@router.post(
    "/auth/register",
    status_code=status.HTTP_201_CREATED,
    response_model=UserCreatedResponse,
)
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


@router.get("/me", response_model=CurrentUserResponse)
def api_me(user: User = Depends(current_user)) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": _iso(user.created_at),
        "feedback_mode": settings.feedback_policy,
    }


@router.get("/tokens", response_model=list[TokenMetadataResponse])
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


@router.post(
    "/tokens",
    status_code=status.HTTP_201_CREATED,
    response_model=TokenCreatedResponse,
)
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
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
    db.commit()
    logger.info("Revoked API token %s for user=%s", token.id, user.id)
    return Response(status_code=204)


@router.get(
    "/problems",
    response_model=list[ProblemSummaryResponse],
    responses={
        200: {
            "description": "Existing array response; pagination headers appear only when page is provided.",
            "headers": {
                name: {
                    "description": "Only present for an explicit paginated request.",
                    "schema": {"type": "integer", "minimum": minimum},
                }
                for name, minimum in (
                    ("X-Total-Count", 0),
                    ("X-Page", 1),
                    ("X-Page-Size", PROBLEM_PAGE_SIZE),
                    ("X-Total-Pages", 1),
                )
            },
        }
    },
)
def list_problems(
    response: Response,
    page: int | None = Query(default=None, ge=1),
    sort: ProblemSort = "default",
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = problem_list_query(q, sort, recent=True)
    if page is not None:
        total = db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        )
        pages = max(1, (total + PROBLEM_PAGE_SIZE - 1) // PROBLEM_PAGE_SIZE)
        response.headers.update(
            {
                "X-Total-Count": str(total),
                "X-Page": str(page),
                "X-Page-Size": str(PROBLEM_PAGE_SIZE),
                "X-Total-Pages": str(pages),
            }
        )
        if page > pages:
            return []
        query = query.offset((page - 1) * PROBLEM_PAGE_SIZE).limit(PROBLEM_PAGE_SIZE)
    problems = db.scalars(query).all()
    return [_problem_summary(problem) for problem in problems]


@router.get("/problems/{problem_id}", response_model=ProblemDetailResponse)
def get_problem(problem_id: str, db: Session = Depends(get_db)) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return _problem_detail(problem)


@router.get("/agent/problems/{problem_id}", response_model=AgentProblemResponse)
def get_agent_problem(
    problem_id: str, _user: User = Depends(bearer_user), db: Session = Depends(get_db)
) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return _problem_detail(problem, agent=True)


@router.post(
    "/submissions",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SubmissionCreatedResponse,
)
def create_submission(
    payload: SubmissionCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    idempotency_key: str | None = Header(
        default=None,
        max_length=128,
        description="Optional opaque request key; reuse the same key and payload after a transport timeout.",
    ),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    with submission_queue_lock:
        submission = enqueue_submission(
            db, user, payload, limits=settings, idempotency_key=idempotency_key
        )
    # A replay returns the original acceptance response, not the current judge state.
    return {"submission_id": submission.id, "status": "QUEUED"}


@router.post(
    "/manage/submissions/{submission_id}/rejudge",
    status_code=202,
    response_model=SubmissionCreatedResponse,
)
def api_rejudge(
    submission_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    require_session_csrf(request, authorization)
    submission = rejudge_submission(db, submission_id, user)
    return {"submission_id": submission.id, "status": submission.status}


@router.get("/submissions/{submission_id}", response_model=SubmissionDetailResponse)
def get_submission(
    submission_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None or (
        submission.user_id != user.id and not can(user, Permission.SUBMISSIONS)
    ):
        raise HTTPException(status_code=404, detail="Submission not found")
    return _submission_detail(submission)


@router.get(
    "/agent/submissions/{submission_id}/feedback",
    response_model=FeedbackResponse,
    response_model_exclude_unset=True,
)
def get_agent_feedback(
    submission_id: int,
    user: User = Depends(bearer_user),
    db: Session = Depends(get_db),
) -> dict:
    submission = db.get(Submission, submission_id)
    if submission is None or (
        submission.user_id != user.id and not can(user, Permission.SUBMISSIONS)
    ):
        raise HTTPException(status_code=404, detail="Submission not found")
    return feedback_response(
        submission,
        settings.feedback_policy,
        allow_hidden=can(user, Permission.SUBMISSIONS),
    )


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


@router.post("/runs", response_model=CustomRunResponse)
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


@router.post(
    "/admin/problems",
    status_code=status.HTTP_201_CREATED,
    response_model=ProblemDetailResponse,
)
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
    values = _problem_write_values(payload)
    problem = Problem(**values, created_by=admin.id)
    db.add(problem)
    db.commit()
    return _problem_detail(problem)


@router.put("/admin/problems/{problem_id}", response_model=ProblemDetailResponse)
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
    values = _problem_write_values(payload)
    values.pop("id")
    if "checker" not in payload.model_fields_set:
        for field in ("checker", "checker_name", "checker_bundle", "checker_sha256"):
            values.pop(field, None)
    update_problem(db, problem, values)
    return _problem_detail(problem)


def _problem_write_values(payload: ProblemCreate) -> dict:
    values = payload.model_dump(exclude={"checker_source"})
    values.update(checker_name=None, checker_bundle=None, checker_sha256=None)
    if payload.checker_source is not None:
        try:
            encoded = CheckerBundle(
                "checker.cpp", {"checker.cpp": payload.checker_source}
            ).serialize()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        values.update(
            checker_name="checker.cpp",
            checker_bundle=encoded,
            checker_sha256=hashlib.sha256(encoded.encode()).hexdigest(),
        )
    return values


@router.get("/admin/problems/{problem_id}/checker")
def admin_checker_metadata(
    problem_id: str,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return {
        "checker": problem.checker,
        "name": problem.checker_name,
        "sha256": problem.checker_sha256,
    }


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


@router.get(
    "/admin/problems/{problem_id}/testcases",
    response_model=list[TestCaseResponse],
)
def admin_list_testcases(
    problem_id: str,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return [_testcase_detail(testcase) for testcase in problem.testcases]


@router.post(
    "/admin/problems/{problem_id}/testcases",
    status_code=status.HTTP_201_CREATED,
    response_model=TestCaseResponse,
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


@router.put(
    "/admin/problems/{problem_id}/testcases/{testcase_id}",
    response_model=TestCaseResponse,
)
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
