from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from minioj.contests import (
    contest_problems,
    contest_status,
    get_contest,
    join_contest,
    problem_label,
    save_contest,
    standings,
    submit_to_contest,
    utc,
)
from minioj.database import get_db
from minioj.models import Contest, ContestParticipant, User, utcnow
from minioj.schemas import (
    ContestStandingsResponse,
    SubmissionCreate,
    SubmissionCreatedResponse,
)
from minioj.server.api import API_ERROR_RESPONSES
from minioj.server.dependencies import current_user, optional_user, require_session_csrf
from minioj.server.web import (
    _check_form_csrf,
    _context,
    _redirect,
    _require_admin,
    _require_user,
    _user,
    templates,
)

router = APIRouter(include_in_schema=False)
api_router = APIRouter(
    prefix="/api/v1", tags=["contests"], responses=API_ERROR_RESPONSES
)


def contest_form_time(value: datetime) -> str:
    # datetime-local accepts at most three fractional second digits.
    return utc(value).replace(tzinfo=None).isoformat(timespec="milliseconds")


def _restore_form_time_precision(submitted: datetime, stored: datetime) -> datetime:
    # A browser round trip must not truncate an unchanged legacy timestamp.
    stored_utc = utc(stored)
    visible = stored_utc.replace(microsecond=stored_utc.microsecond // 1000 * 1000)
    return stored if utc(submitted) == visible else submitted


templates.env.globals.update(
    contest_status=contest_status,
    problem_label=problem_label,
    contest_form_time=contest_form_time,
)


def render(request, db, name, **values):
    return templates.TemplateResponse(
        request=request, name=name, context=_context(request, db, **values)
    )


@router.get("/contests")
def list_contests(request: Request, page: int = 1, db: Session = Depends(get_db)):
    if page < 1:
        raise HTTPException(status_code=422, detail="Invalid page")
    rows = db.scalars(
        select(Contest)
        .where(Contest.deleted_at.is_(None))
        .order_by(Contest.start_time.desc())
        .offset((page - 1) * 100)
        .limit(100)
    ).all()
    groups = {
        state: [c for c in rows if contest_status(c) == state]
        for state in ("RUNNING", "UPCOMING", "ENDED")
    }
    return render(
        request,
        db,
        "contests.html",
        groups=groups,
        page=page,
        has_next=len(rows) == 100,
    )


@router.get("/contests/{contest_id}")
def detail(contest_id: int, request: Request, db: Session = Depends(get_db)):
    contest = get_contest(db, contest_id)
    user = _user(request, db)
    joined = (
        user
        and db.scalar(
            select(ContestParticipant.id).where(
                ContestParticipant.contest_id == contest.id,
                ContestParticipant.user_id == user.id,
            )
        )
        is not None
    )
    return render(
        request,
        db,
        "contest_detail.html",
        contest=contest,
        problems=contest_problems(contest, user),
        joined=joined,
    )


@router.post("/contests/{contest_id}/join")
async def join(contest_id: int, request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    contest = get_contest(db, contest_id, lock=True)
    if contest_status(contest) == "ENDED":
        raise HTTPException(status_code=409, detail="Contest has ended")
    join_contest(db, contest, user)
    db.commit()
    return _redirect(request, f"/contests/{contest.id}")


@router.get("/contests/{contest_id}/problems/{label}")
def contest_problem(
    contest_id: int, label: str, request: Request, db: Session = Depends(get_db)
):
    contest = get_contest(db, contest_id)
    row = next(
        (
            r
            for r in contest_problems(contest, _user(request, db))
            if problem_label(r.position) == label.upper()
        ),
        None,
    )
    if row is None or row.problem.deleted_at:
        raise HTTPException(status_code=404, detail="Contest problem not found")
    return render(
        request,
        db,
        "problem_detail.html",
        problem=row.problem,
        contest=contest,
        contest_label=label.upper(),
    )


@router.get("/contests/{contest_id}/standings")
def standing_page(contest_id: int, request: Request, db: Session = Depends(get_db)):
    contest = get_contest(db, contest_id)
    problems = contest_problems(contest, _user(request, db))
    return render(
        request,
        db,
        "contest_standings.html",
        contest=contest,
        problems=problems,
        rows=standings(db, contest, problems=problems),
    )


@api_router.get(
    "/contests/{contest_id}/standings", response_model=ContestStandingsResponse
)
def standing_data(
    contest_id: int,
    user: User | None = Depends(optional_user),
    db: Session = Depends(get_db),
):
    contest = get_contest(db, contest_id)
    problems = contest_problems(contest, user)
    return {
        "contest_id": contest.id,
        "problems": [
            {"problem_id": p.problem_id, "label": problem_label(p.position)}
            for p in problems
        ],
        "rows": standings(db, contest, problems=problems),
    }


@api_router.post(
    "/contests/{contest_id}/submissions",
    response_model=SubmissionCreatedResponse,
    status_code=202,
)
def contest_submission(
    contest_id: int,
    payload: SubmissionCreate,
    request: Request,
    authorization: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_session_csrf(request, authorization)
    submission = submit_to_contest(db, contest_id, user, payload)
    return {"submission_id": submission.id, "status": submission.status}


@router.get("/manage/contests")
def manage_contests(request: Request, page: int = 1, db: Session = Depends(get_db)):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if page < 1:
        raise HTTPException(status_code=422, detail="Invalid page")
    return render(
        request,
        db,
        "management_contests.html",
        contests=db.scalars(
            select(Contest)
            .where(Contest.deleted_at.is_(None))
            .order_by(Contest.id.desc())
            .offset((page - 1) * 100)
            .limit(100)
        ).all(),
        page=page,
    )


@router.get("/manage/contests/new")
@router.get("/manage/contests/{contest_id}/edit")
def contest_form(
    request: Request, contest_id: int | None = None, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    contest = get_contest(db, contest_id) if contest_id is not None else None
    return render(request, db, "contest_form.html", contest=contest)


@router.post("/manage/contests/new")
@router.post("/manage/contests/{contest_id}/edit")
async def edit_contest(
    request: Request, contest_id: int | None = None, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        start = datetime.fromisoformat(str(form.get("start_time", "")))
        end = datetime.fromisoformat(str(form.get("end_time", "")))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="Invalid UTC start/end time"
        ) from exc
    if contest_id is not None:
        existing = get_contest(db, contest_id, lock=True)
        start = _restore_form_time_precision(start, existing.start_time)
        end = _restore_form_time_precision(end, existing.end_time)
    problem_ids = str(form.get("problem_ids", "")).replace(",", " ").split()
    contest = save_contest(
        db,
        user,
        str(form.get("title", "")),
        str(form.get("description", "")),
        start,
        end,
        problem_ids,
        contest_id,
    )
    return _redirect(request, f"/manage/contests/{contest.id}/edit")


@router.post("/manage/contests/{contest_id}/delete")
async def delete_contest(
    contest_id: int, request: Request, db: Session = Depends(get_db)
):
    user = _require_admin(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    contest = get_contest(db, contest_id, lock=True)
    if contest_status(contest) == "RUNNING":
        raise HTTPException(status_code=409, detail="Cannot delete a running contest")
    contest.deleted_at = utcnow()
    db.commit()
    return _redirect(request, "/manage/contests")
