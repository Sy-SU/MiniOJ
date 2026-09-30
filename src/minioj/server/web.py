from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.database import get_db
from minioj.models import ApiToken, Problem, Submission, User
from minioj.problems import add_testcase, remove_problem_files
from minioj.security import (
    PROBLEM_ID_RE,
    USERNAME_RE,
    create_api_token,
    csrf_token,
    hash_password,
    is_reserved_username,
    valid_csrf,
    validate_password,
    verify_password,
)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(settings.templates_dir))


def _user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    user = db.get(User, int(user_id)) if user_id else None
    if user and user.is_active:
        return user
    request.session.pop("user_id", None)
    return None


def _context(request: Request, db: Session, **values: object) -> dict:
    return {
        "request": request,
        "current_user": _user(request, db),
        "csrf_token": csrf_token(request.session),
        "flash": request.session.pop("flash", None),
        "base_path": str(request.scope.get("root_path", "")).rstrip("/"),
        **values,
    }


def _flash(request: Request, message: str, kind: str = "info") -> None:
    request.session["flash"] = {"message": message, "kind": kind}


def _external_path(request: Request, path: str) -> str:
    base_path = str(request.scope.get("root_path", "")).rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_path}{path}"


def _redirect(request: Request, path: str) -> RedirectResponse:
    return RedirectResponse(_external_path(request, path), status_code=303)


def _check_form_csrf(request: Request, form: object) -> None:
    supplied = form.get("csrf_token") if hasattr(form, "get") else None
    if not valid_csrf(request.session, str(supplied) if supplied else None):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def _require_user(request: Request, db: Session) -> User | RedirectResponse:
    user = _user(request, db)
    if user is None:
        _flash(request, "Please sign in to continue.", "warning")
        return _redirect(request, "/login")
    return user


def _require_admin(request: Request, db: Session) -> User | RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    recent = db.scalars(
        select(Problem).order_by(Problem.created_at.desc()).limit(8)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context=_context(request, db, problems=recent),
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="register.html", context=_context(request, db)
    )


@router.post("/register")
async def register(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    _check_form_csrf(request, form)
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    confirmation = str(form.get("password_confirmation", ""))
    error = None
    if not USERNAME_RE.fullmatch(username):
        error = "Username must be 3-50 letters, numbers, dots, dashes, or underscores."
    elif is_reserved_username(username):
        error = "This username is reserved for an administrator. Please choose another."
    elif "@" not in email or len(email) > 255:
        error = "Please enter a valid email address."
    elif password != confirmation:
        error = "Passwords do not match."
    else:
        error = validate_password(password)
    if error:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=_context(request, db, error=error, username=username, email=email),
            status_code=422,
        )
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        role="user",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=_context(
                request,
                db,
                error="Username or email already exists.",
                username=username,
                email=email,
            ),
            status_code=409,
        )
    db.refresh(user)
    request.session.clear()
    request.session["user_id"] = user.id
    _flash(request, "Welcome to MiniOJ!")
    return _redirect(request, "/problems")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="login.html", context=_context(request, db)
    )


@router.post("/login")
async def login(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    form = await request.form()
    _check_form_csrf(request, form)
    identity = str(form.get("identity", "")).strip()
    password = str(form.get("password", ""))
    user = db.scalar(
        select(User).where(
            (User.username == identity) | (User.email == identity.lower())
        )
    )
    if (
        user is None
        or not user.is_active
        or not verify_password(password, user.password_hash)
    ):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context=_context(
                request,
                db,
                error="Invalid credentials or inactive account.",
                identity=identity,
            ),
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    _flash(request, f"Welcome back, {user.username}!")
    return _redirect(request, "/problems")


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    form = await request.form()
    _check_form_csrf(request, form)
    request.session.clear()
    return _redirect(request, "/")


@router.get("/problems", response_class=HTMLResponse)
def problems(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    rows = db.scalars(select(Problem).order_by(Problem.id)).all()
    return templates.TemplateResponse(
        request=request,
        name="problems.html",
        context=_context(request, db, problems=rows),
    )


@router.get("/problems/{problem_id}", response_class=HTMLResponse)
def problem_detail(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return templates.TemplateResponse(
        request=request,
        name="problem_detail.html",
        context=_context(request, db, problem=problem),
    )


@router.get("/submissions", response_class=HTMLResponse)
def submissions(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    query = select(Submission).order_by(Submission.created_at.desc()).limit(200)
    if user.role != "admin":
        query = query.where(Submission.user_id == user.id)
    rows = db.scalars(query).all()
    return templates.TemplateResponse(
        request=request,
        name="submissions.html",
        context=_context(request, db, submissions=rows),
    )


@router.get("/submissions/{submission_id}", response_class=HTMLResponse)
def submission_detail(
    submission_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    submission = db.get(Submission, submission_id)
    if submission is None or (submission.user_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=404, detail="Submission not found")
    compile_result = (
        json.loads(submission.compile_result) if submission.compile_result else None
    )
    judge_result = (
        json.loads(submission.judge_result) if submission.judge_result else None
    )
    return templates.TemplateResponse(
        request=request,
        name="submission_detail.html",
        context=_context(
            request,
            db,
            submission=submission,
            compile_result=compile_result,
            judge_result=judge_result,
        ),
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    tokens = db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user.id)
        .order_by(ApiToken.created_at.desc())
    ).all()
    revealed_token = request.session.pop("revealed_token", None)
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=_context(request, db, tokens=tokens, revealed_token=revealed_token),
    )


@router.post("/settings/profile")
async def update_profile(
    request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    email = str(form.get("email", "")).strip().lower()
    if "@" not in email or len(email) > 255:
        _flash(request, "Please enter a valid email.", "error")
        return _redirect(request, "/settings")
    user.email = email
    try:
        db.commit()
        _flash(request, "Profile updated.")
    except IntegrityError:
        db.rollback()
        _flash(request, "That email is already in use.", "error")
    return _redirect(request, "/settings")


@router.post("/settings/password")
async def update_password(
    request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    current = str(form.get("current_password", ""))
    password = str(form.get("password", ""))
    confirmation = str(form.get("password_confirmation", ""))
    if not verify_password(current, user.password_hash):
        _flash(request, "Current password is incorrect.", "error")
    elif password != confirmation:
        _flash(request, "New passwords do not match.", "error")
    elif error := validate_password(password):
        _flash(request, error, "error")
    else:
        user.password_hash = hash_password(password)
        db.commit()
        _flash(request, "Password changed.")
    return _redirect(request, "/settings")


@router.post("/settings/tokens")
async def web_create_token(
    request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    name = str(form.get("name", "")).strip()
    if not name or len(name) > 100:
        _flash(request, "Token name must be 1-100 characters.", "error")
        return _redirect(request, "/settings")
    token_id, raw, digest, expires_at = create_api_token(settings.token_default_days)
    db.add(
        ApiToken(
            id=token_id,
            user_id=user.id,
            name=name,
            token_hash=digest,
            expires_at=expires_at,
        )
    )
    db.commit()
    request.session["revealed_token"] = raw
    _flash(
        request, "Token created. Copy it now; it will not be shown again.", "success"
    )
    return _redirect(request, "/settings")


@router.post("/settings/tokens/{token_id}/revoke")
async def web_revoke_token(
    token_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    form = await request.form()
    _check_form_csrf(request, form)
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(status_code=404, detail="Token not found")
    token.revoked_at = token.revoked_at or datetime.now(UTC)
    db.commit()
    _flash(request, "Token revoked.")
    return _redirect(request, "/settings")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problems = db.scalars(select(Problem).order_by(Problem.id)).all()
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    recent = db.scalars(
        select(Submission).order_by(Submission.created_at.desc()).limit(25)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="admin.html",
        context=_context(
            request, db, problems=problems, users=users, submissions=recent
        ),
    )


@router.get("/admin/problems/new", response_class=HTMLResponse)
def new_problem_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    return templates.TemplateResponse(
        request=request,
        name="problem_form.html",
        context=_context(request, db, problem=None, action="Create"),
    )


def _problem_form_values(form: object) -> dict:
    def value(name: str, default: str = "") -> str:
        item = form.get(name, default) if hasattr(form, "get") else default
        return str(item).strip()

    rating_text = value("rating")
    return {
        "id": value("id"),
        "title": value("title"),
        "statement": value("statement"),
        "input_specification": value("input_specification"),
        "output_specification": value("output_specification"),
        "notes": value("notes"),
        "time_limit_ms": int(value("time_limit_ms", "2000")),
        "memory_limit_mb": int(value("memory_limit_mb", "256")),
        "source": value("source") or None,
        "source_id": value("source_id") or None,
        "source_url": value("source_url") or None,
        "rating": int(rating_text) if rating_text else None,
        "tags": value("tags"),
    }


def _problem_values_error(values: dict) -> str | None:
    if not PROBLEM_ID_RE.fullmatch(str(values["id"])):
        return "Problem id must be a lowercase slug of at least 3 characters."
    if not values["title"] or not values["statement"]:
        return "Title and statement are required."
    if len(str(values["title"])) > 255:
        return "Title must contain at most 255 characters."
    if not 100 <= int(values["time_limit_ms"]) <= 30_000:
        return "Time limit must be between 100 and 30000 ms."
    if not 16 <= int(values["memory_limit_mb"]) <= 2048:
        return "Memory limit must be between 16 and 2048 MB."
    if values["source"] and len(str(values["source"])) > 100:
        return "Source must contain at most 100 characters."
    if values["source_id"] and len(str(values["source_id"])) > 100:
        return "Source ID must contain at most 100 characters."
    if values["source_url"] and len(str(values["source_url"])) > 1000:
        return "Source URL must contain at most 1000 characters."
    return None


@router.post("/admin/problems/new")
async def new_problem(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        values = _problem_form_values(form)
    except ValueError:
        values = {"id": str(form.get("id", "")), "title": str(form.get("title", ""))}
        error = "Limits and rating must be valid numbers."
    else:
        error = None
    if not error:
        error = _problem_values_error(values)
    if not error and db.get(Problem, values["id"]):
        error = "Problem id already exists."
    if error:
        return templates.TemplateResponse(
            request=request,
            name="problem_form.html",
            context=_context(request, db, problem=values, action="Create", error=error),
            status_code=422,
        )
    problem = Problem(**values, created_by=admin.id)
    db.add(problem)
    db.commit()
    _flash(request, "Problem created. Add at least one testcase next.")
    return _redirect(request, f"/admin/problems/{problem.id}/edit")


@router.get("/admin/problems/{problem_id}/edit", response_class=HTMLResponse)
def edit_problem_page(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return templates.TemplateResponse(
        request=request,
        name="problem_form.html",
        context=_context(request, db, problem=problem, action="Update"),
    )


@router.post("/admin/problems/{problem_id}/edit")
async def edit_problem(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        values = _problem_form_values(form)
    except ValueError:
        _flash(request, "Limits and rating must be valid numbers.", "error")
        return _redirect(request, f"/admin/problems/{problem_id}/edit")
    if error := _problem_values_error(values):
        _flash(request, error, "error")
        return _redirect(request, f"/admin/problems/{problem_id}/edit")

    for key, value in values.items():
        if key != "id":
            setattr(problem, key, value)
    db.commit()
    _flash(request, "Problem updated.")
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcases")
async def web_add_testcase(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        add_testcase(
            db,
            problem,
            str(form.get("type", "hidden")),
            str(form.get("input", "")),
            str(form.get("output", "")),
        )
        _flash(request, "Testcase added.")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/delete")
async def web_delete_problem(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = await request.form()
    _check_form_csrf(request, form)
    problem = db.get(Problem, problem_id)
    if problem is None:
        raise HTTPException(status_code=404, detail="Problem not found")
    has_submission = db.scalar(
        select(Submission.id).where(Submission.problem_id == problem_id).limit(1)
    )
    if has_submission:
        _flash(request, "Cannot delete a problem that has submissions.", "error")
    else:
        db.delete(problem)
        db.commit()
        remove_problem_files(problem_id)
        _flash(request, "Problem deleted.")
    return _redirect(request, "/admin")


@router.post("/admin/users/{user_id}")
async def update_user(
    user_id: int, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = await request.form()
    _check_form_csrf(request, form)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    role = str(form.get("role", "user"))
    active = form.get("is_active") == "on"
    if role not in {"user", "admin"}:
        raise HTTPException(status_code=422, detail="Invalid role")
    if user.id == admin.id and (role != "admin" or not active):
        _flash(request, "You cannot demote or deactivate your own account.", "error")
    else:
        user.role = role
        user.is_active = active
        db.commit()
        _flash(request, f"Updated user {user.username}.")
    return _redirect(request, "/admin")
