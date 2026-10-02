from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.database import get_db
from minioj.feedback import submission_feedback
from minioj.models import Contest, Problem, Submission, SubmissionJudgeRun, User
from minioj.permissions import Permission, can
from minioj.server.web import (
    _check_form_csrf,
    _context,
    _redirect,
    _require_admin,
    _require_system,
    _require_user,
    submission_detail,
    templates,
    update_user,
)
from minioj.submissions import rejudge_submission

router = APIRouter(include_in_schema=False)


def render(request, db, name, **values):
    return templates.TemplateResponse(
        request=request, name=name, context=_context(request, db, **values)
    )


@router.get("/manage")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    def count(model, *conditions):
        return db.scalar(select(func.count()).select_from(model).where(*conditions))

    total = count(Submission)
    accepted = count(
        Submission, Submission.status == "FINISHED", Submission.verdict == "AC"
    )
    stats = {
        "Total Users": count(User),
        "Total Problems": count(Problem, Problem.deleted_at.is_(None)),
        "Total Submissions": total,
        "Total Contests": count(Contest, Contest.deleted_at.is_(None)),
        "Today Submissions (UTC)": count(Submission, Submission.created_at >= today),
        "AC Rate": f"{accepted / total * 100:.1f}%" if total else "0.0%",
        "Running Contests": count(
            Contest,
            Contest.deleted_at.is_(None),
            Contest.start_time <= now,
            Contest.end_time > now,
        ),
        "Upcoming Contests": count(
            Contest, Contest.deleted_at.is_(None), Contest.start_time > now
        ),
        "Queued Submissions": count(Submission, Submission.status == "QUEUED"),
        "Running Submissions": count(
            Submission, Submission.status.in_(["COMPILING", "RUNNING"])
        ),
    }
    return render(
        request,
        db,
        "management_dashboard.html",
        stats=stats,
        submissions=db.scalars(
            select(Submission).order_by(Submission.id.desc()).limit(15)
        ).all(),
        problems=db.scalars(
            select(Problem)
            .where(Problem.deleted_at.is_(None))
            .order_by(Problem.created_at.desc())
            .limit(10)
        ).all(),
        contests=db.scalars(
            select(Contest)
            .where(Contest.deleted_at.is_(None))
            .order_by(Contest.created_at.desc())
            .limit(10)
        ).all(),
        rejudges=db.scalars(
            select(SubmissionJudgeRun)
            .where(SubmissionJudgeRun.trigger_type == "rejudge")
            .order_by(SubmissionJudgeRun.id.desc())
            .limit(10)
        ).all(),
    )


@router.get("/manage/problems")
def problem_management(
    request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if page < 1:
        raise HTTPException(status_code=422, detail="Invalid page")
    query = select(Problem).where(Problem.deleted_at.is_(None))
    if q:
        query = query.where(
            Problem.id.contains(q, autoescape=True)
            | Problem.title.contains(q, autoescape=True)
        )
    rows = db.scalars(
        query.order_by(Problem.id).offset((page - 1) * 100).limit(100)
    ).all()
    return render(
        request, db, "management_problems.html", problems=rows, q=q, page=page
    )


@router.get("/manage/submissions")
def submission_management(
    request: Request,
    submission_id: str = "",
    user: str = "",
    problem: str = "",
    contest: str = "",
    language: str = "",
    status: str = "",
    verdict: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    actor = _require_admin(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    if page < 1:
        raise HTTPException(status_code=422, detail="Invalid page")
    for value in (submission_id, contest):
        if value and (not value.isdecimal() or len(value) > 18):
            raise HTTPException(
                status_code=422, detail="Submission/contest ID must be an integer"
            )
    query = select(Submission).join(User)
    for value, column in (
        (submission_id, Submission.id),
        (problem, Submission.problem_id),
        (contest, Submission.contest_id),
        (language, Submission.language),
        (status, Submission.status),
        (verdict, Submission.verdict),
    ):
        if value is not None and value != "":
            query = query.where(column == value)
    if user:
        query = query.where(func.lower(User.username) == user.lower())
    rows = db.scalars(
        query.order_by(Submission.id.desc()).offset((page - 1) * 100).limit(100)
    ).all()
    return render(
        request,
        db,
        "management_submissions.html",
        submissions=rows,
        page=page,
        filters={
            "submission_id": submission_id,
            "user": user,
            "problem": problem,
            "contest": contest,
            "language": language,
            "status": status,
            "verdict": verdict,
        },
    )


@router.get("/manage/submissions/{submission_id}")
def managed_submission(
    submission_id: int, request: Request, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return submission_detail(submission_id, request, db)


@router.post("/manage/submissions/{submission_id}/rejudge")
async def web_rejudge(
    submission_id: int, request: Request, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    rejudge_submission(db, submission_id, user)
    return _redirect(request, f"/manage/submissions/{submission_id}")


@router.get("/submissions/{submission_id}/history/{run_id}")
@router.get("/manage/submissions/{submission_id}/history/{run_id}")
def judge_history(
    submission_id: int, run_id: int, request: Request, db: Session = Depends(get_db)
):
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if "/manage/" in request.url.path and not can(user, Permission.SUBMISSIONS):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    run = db.get(SubmissionJudgeRun, run_id)
    if (
        run is None
        or run.submission_id != submission_id
        or (run.submission.user_id != user.id and not can(user, Permission.SUBMISSIONS))
    ):
        raise HTTPException(status_code=404, detail="Judge run not found")
    snapshot = SimpleNamespace(
        status=run.status,
        verdict=run.verdict,
        compile_result=run.compile_result,
        judge_result=run.judge_result,
        problem=run.submission.problem,
        problem_revision=run.problem_revision,
    )
    result = submission_feedback(
        snapshot,
        settings.feedback_policy,
        allow_hidden=can(user, Permission.SUBMISSIONS),
        include_success_compile=True,
    )
    if run.verdict == "IE":
        result = {"verdict": "IE", "summary": "The judge could not complete this run."}
    return render(request, db, "judge_history.html", run=run, result=result)


@router.get("/manage/users")
def users(request: Request, q: str = "", page: int = 1, db: Session = Depends(get_db)):
    actor = _require_system(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    if page < 1:
        raise HTTPException(status_code=422, detail="Invalid page")
    query = select(User)
    if q:
        query = query.where(User.username.contains(q, autoescape=True))
    return render(
        request,
        db,
        "management_users.html",
        users=db.scalars(
            query.order_by(User.id).offset((page - 1) * 100).limit(100)
        ).all(),
        q=q,
        page=page,
    )


router.add_api_route("/manage/users/{user_id}", update_user, methods=["POST"])


@router.get("/manage/system")
def system(request: Request, db: Session = Depends(get_db)):
    actor = _require_system(request, db)
    if isinstance(actor, RedirectResponse):
        return actor
    info = {
        "Database": db.get_bind().dialect.name,
        "Database check": "OK" if db.scalar(select(1)) == 1 else "Unavailable",
        "Data directory": "Available" if settings.data_dir.is_dir() else "Missing",
        "Job directory": "Available" if settings.jobs_dir.is_dir() else "Missing",
        "Feedback Mode": settings.feedback_policy,
        "Judge": "Host Worker / Docker Sandbox",
        "Queue capacity": settings.max_queued_submissions,
    }
    return render(request, db, "management_system.html", info=info)
