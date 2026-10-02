from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from minioj.config import settings
from minioj.database import get_db
from minioj.feedback import submission_feedback, submission_testcase_sections
from minioj.models import ApiToken, Problem, Submission, TestcaseBuild, User
from minioj.permissions import Permission, can, require_permission
from minioj.polygon import import_polygon, parse_polygon, problem_asset_path
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
from minioj.rendering import render_markdown
from minioj.security import (
    EMAIL_MAX_LENGTH,
    PASSWORD_MAX_BYTES,
    PROBLEM_ID_RE,
    USERNAME_MAX_LENGTH,
    USERNAME_RE,
    create_api_token,
    csrf_token,
    hash_password,
    is_reserved_username,
    mask_token,
    password_within_limit,
    valid_csrf,
    valid_email,
    validate_password,
    verify_password,
)
from minioj.server.uploads import (
    TestcaseFileTooLarge,
    testcase_form,
    uploaded_bytes,
)
from minioj.testcase_builds import (
    queue_generator_build,
    queue_input_build,
    save_standard_solution,
)

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(settings.templates_dir))
logger = logging.getLogger("minioj.web")


@pass_context
def _markdown_for_request(context, value):
    request = context.get("request")
    return render_markdown(value, request.scope.get("root_path", "") if request else "")


templates.env.filters["markdown"] = _markdown_for_request
templates.env.globals["can"] = can


def _user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    user = db.get(User, int(user_id)) if user_id else None
    if user and user.is_active:
        return user
    request.session.pop("user_id", None)
    return None


def _context(request: Request, db: Session, **values: object) -> dict:
    path = request.url.path.removeprefix(str(request.scope.get("root_path", "")))
    management = path.startswith(("/manage", "/admin"))
    return {
        "layout_template": "management_base.html" if management else "base.html",
        "manage_section": path.split("/")[2]
        if management and len(path.split("/")) > 2
        else "dashboard",
        "username_max_length": USERNAME_MAX_LENGTH,
        "email_max_length": EMAIL_MAX_LENGTH,
        "password_max_bytes": PASSWORD_MAX_BYTES,
        "testcase_file_limit_bytes": settings.testcase_file_limit_bytes,
        "source_limit_bytes": settings.source_limit_bytes,
        "generator_max_cases": settings.generator_max_cases,
        "polygon_archive_limit_bytes": settings.polygon_archive_limit_bytes,
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
    if "/manage" in request.url.path and path.startswith("/admin"):
        path = "/manage" + path[len("/admin") :]
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
    return require_permission(user, Permission.CONTENT)


def _require_system(request: Request, db: Session) -> User | RedirectResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    return require_permission(user, Permission.SYSTEM)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    recent = db.scalars(
        select(Problem)
        .where(Problem.deleted_at.is_(None))
        .order_by(Problem.created_at.desc())
        .limit(8)
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
        error = "Username must contain 3-10 English letters (A-Z or a-z)."
    elif is_reserved_username(username):
        error = "This username is reserved for an administrator. Please choose another."
    elif not valid_email(email):
        error = "Please enter a valid email address."
    elif password != confirmation:
        error = "Passwords do not match."
    else:
        error = validate_password(password)
    if error:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context=_context(
                request,
                db,
                error=error,
                username=username[:USERNAME_MAX_LENGTH],
                email=email[:EMAIL_MAX_LENGTH],
            ),
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
    user = None
    if len(identity) <= EMAIL_MAX_LENGTH and password_within_limit(password):
        user = db.scalar(
            select(User).where(
                (func.lower(User.username) == identity.lower())
                | (User.email == identity.lower())
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
                identity=identity[:EMAIL_MAX_LENGTH],
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
def problems(
    request: Request,
    page: int = Query(default=1, ge=1),
    sort: ProblemSort = "default",
    q: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    query = problem_list_query(q, sort)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    pages = max(1, (total + PROBLEM_PAGE_SIZE - 1) // PROBLEM_PAGE_SIZE)
    rows = (
        db.scalars(
            query.offset((page - 1) * PROBLEM_PAGE_SIZE).limit(PROBLEM_PAGE_SIZE)
        ).all()
        if page <= pages
        else []
    )
    return templates.TemplateResponse(
        request=request,
        name="problems.html",
        context=_context(
            request,
            db,
            problems=rows,
            total=total,
            page=page,
            pages=pages,
            sort=sort,
            q=q,
            previous_url=str(request.url.include_query_params(page=page - 1)),
            next_url=str(request.url.include_query_params(page=page + 1)),
        ),
    )


@router.get("/problems/{problem_id}", response_class=HTMLResponse)
def problem_detail(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        return templates.TemplateResponse(
            request=request,
            name="problem_not_found.html",
            context=_context(request, db),
            status_code=404 if problem is None else 410,
        )
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
    if not can(user, Permission.SUBMISSIONS):
        query = query.where(Submission.user_id == user.id)
    rows = db.scalars(query).all()
    return templates.TemplateResponse(
        request=request,
        name="submissions.html",
        context=_context(request, db, submissions=rows),
    )


@router.get("/submissions/{submission_id}", response_class=HTMLResponse)
def submission_detail(
    submission_id: int, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    submission = db.get(Submission, submission_id)
    if submission is None or (
        submission.user_id != user.id and not can(user, Permission.SUBMISSIONS)
    ):
        raise HTTPException(status_code=404, detail="Submission not found")
    judge_result = submission_feedback(
        submission,
        settings.feedback_policy,
        allow_hidden=can(user, Permission.SUBMISSIONS),
    )
    testcase_sections = submission_testcase_sections(
        submission,
        judge_result,
        allow_hidden=can(user, Permission.SUBMISSIONS),
        show_previews=settings.feedback_policy == "full",
    )
    compile_result = judge_result.get("compile")
    return templates.TemplateResponse(
        request=request,
        name="submission_detail.html",
        context=_context(
            request,
            db,
            submission=submission,
            compile_result=compile_result,
            judge_result=judge_result,
            testcase_sections=testcase_sections,
            feedback_policy=settings.feedback_policy,
            judge_history=sorted(
                submission.judge_runs, key=lambda run: run.generation, reverse=True
            ),
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
    if not valid_email(email):
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
    if error := validate_password(password):
        _flash(request, error, "error")
    elif not password_within_limit(confirmation):
        _flash(request, "Password confirmation is too long or invalid.", "error")
    elif not verify_password(current, user.password_hash):
        _flash(request, "Current password is incorrect.", "error")
    elif password != confirmation:
        _flash(request, "New passwords do not match.", "error")
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
            token_preview=mask_token(raw),
            expires_at=expires_at,
        )
    )
    db.commit()
    request.session["revealed_token"] = raw
    _flash(
        request, "Token created. Copy it now; it will not be shown again.", "success"
    )
    return _redirect(request, "/settings")


@router.post("/settings/tokens/{token_id}/delete")
async def web_delete_token(
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
    if token.revoked_at is None:
        token.revoked_at = datetime.now(UTC)
    db.commit()
    logger.info("Revoked API token %s for user=%s", token.id, user.id)
    _flash(request, "Token revoked.")
    return _redirect(request, "/settings")


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    return _redirect(request, "/manage")


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


@router.get("/admin/problems/import", response_class=HTMLResponse)
def polygon_import_page(request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    return templates.TemplateResponse(
        request=request, name="polygon_import.html", context=_context(request, db)
    )


@router.post("/admin/problems/import", response_class=HTMLResponse)
async def polygon_import(request: Request, db: Session = Depends(get_db)):
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = None
    try:
        form = await testcase_form(
            request,
            settings.polygon_archive_limit_bytes,
            body_limit_bytes=settings.polygon_archive_limit_bytes + 64 * 1024,
        )
        _check_form_csrf(request, form)
        data = await uploaded_bytes(
            form.get("package_file"), settings.polygon_archive_limit_bytes
        )
        if data is None:
            raise ValueError("Select a Polygon ZIP package.")
        # ZIP parsing and filesystem/database work must not block the event loop.
        from starlette.concurrency import run_in_threadpool

        package = await run_in_threadpool(
            parse_polygon,
            data,
            str(form.get("problem_id", "")),
            str(form.get("language", "auto")),
        )
        problem = await run_in_threadpool(import_polygon, db, package, admin.id)
    except (TestcaseFileTooLarge, MultiPartException, ValueError, OSError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            request=request,
            name="polygon_import.html",
            context=_context(
                request,
                db,
                error=str(exc),
                problem_id=str(form.get("problem_id", "")) if form else "",
                language=str(form.get("language", "auto")) if form else "auto",
            ),
            status_code=413 if isinstance(exc, TestcaseFileTooLarge) else 422,
        )
    finally:
        if form is not None:
            await form.close()
    _flash(
        request,
        f"Polygon problem imported: {len(package.cases)} tests, {package.case_types.count('sample')} samples ({package.language}).",
    )
    return _redirect(request, f"/admin/problems/{problem.id}/edit")


@router.get("/problems/{problem_id}/assets/{name}")
def statement_asset(problem_id: str, name: str, db: Session = Depends(get_db)):
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        path = problem_asset_path(problem_id, name)
    except ValueError:
        raise HTTPException(
            status_code=404, detail="Statement image not found"
        ) from None
    return FileResponse(path, headers={"X-Content-Type-Options": "nosniff"})


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
        return (
            "Problem id must be 3-80 characters using letters, numbers, or "
            "hyphens, and must start and end with a letter or number."
        )
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


@router.post("/admin/problems/preview", response_class=HTMLResponse)
async def preview_problem_markdown(
    request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = await request.form()
    _check_form_csrf(request, form)
    return templates.TemplateResponse(
        request=request,
        name="problem_preview.html",
        context={
            "statement": str(form.get("statement", "")),
            "input_specification": str(form.get("input_specification", "")),
            "output_specification": str(form.get("output_specification", "")),
            "notes": str(form.get("notes", "")),
        },
    )


@router.get("/admin/problems/{problem_id}/edit", response_class=HTMLResponse)
def edit_problem_page(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    return _render_problem_editor(request, db, problem)


def _render_problem_editor(
    request: Request,
    db: Session,
    problem: Problem,
    *,
    status_code: int = 200,
    **values: object,
) -> HTMLResponse:
    testcase_builds = db.scalars(
        select(TestcaseBuild)
        .where(TestcaseBuild.problem_id == problem.id)
        .order_by(TestcaseBuild.created_at.desc())
        .limit(25)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="problem_form.html",
        context=_context(
            request,
            db,
            problem=problem,
            action="Update",
            testcase_builds=testcase_builds,
            **values,
        ),
        status_code=status_code,
    )


@router.post("/admin/problems/{problem_id}/edit")
async def edit_problem(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        ensure_problem_mutable(db, problem.id)
    except ValueError as exc:
        _flash(request, str(exc), "error")
        return _redirect(request, f"/admin/problems/{problem_id}/edit")
    try:
        values = _problem_form_values(form)
    except ValueError:
        _flash(request, "Limits and rating must be valid numbers.", "error")
        return _redirect(request, f"/admin/problems/{problem_id}/edit")
    if error := _problem_values_error(values):
        _flash(request, error, "error")
        return _redirect(request, f"/admin/problems/{problem_id}/edit")

    update_problem(db, problem, values)
    _flash(request, "Problem updated.")
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/standard-solution")
async def upload_standard_solution(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        form = await testcase_form(
            request,
            settings.source_limit_bytes,
            body_limit_bytes=3 * settings.source_limit_bytes + 64 * 1024,
        )
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pasted = form.get("standard_source")
    try:
        _check_form_csrf(request, form)
        source = await uploaded_bytes(
            form.get("standard_file"), settings.source_limit_bytes
        )
        if pasted is not None:
            if not isinstance(pasted, str):
                raise ValueError("Pasted standard solution must be text.")
            if source is not None:
                raise ValueError("Paste source or upload a file, not both at once.")
            source = pasted.encode("utf-8")
        if source is None:
            raise ValueError("Paste a C++20 standard solution or select a source file.")
        save_standard_solution(db, problem, source)
        _flash(request, "Standard solution saved.")
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        return _render_problem_editor(
            request,
            db,
            problem,
            status_code=422,
            error=str(exc),
            standard_source_draft=pasted
            if isinstance(pasted, str)
            else (problem.standard_source or ""),
        )
    finally:
        await form.close()
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcase-builds/input")
async def queue_uploaded_testcase_input(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        form = await testcase_form(
            request,
            settings.testcase_file_limit_bytes,
            body_limit_bytes=settings.testcase_file_limit_bytes + 64 * 1024,
        )
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _check_form_csrf(request, form)
        input_data = await uploaded_bytes(
            form.get("input_file"), settings.testcase_file_limit_bytes
        )
        if input_data is None:
            raise ValueError("Select a testcase input file.")
        build = queue_input_build(
            db,
            problem,
            admin,
            input_data,
            str(form.get("type", "hidden")),
        )
        _flash(request, f"Testcase build #{build.id} queued for the Worker.")
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        _flash(request, str(exc), "error")
    finally:
        await form.close()
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcase-builds/generator")
async def queue_cpp_generator(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        form = await testcase_form(
            request,
            settings.source_limit_bytes,
            body_limit_bytes=settings.source_limit_bytes + 64 * 1024,
        )
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _check_form_csrf(request, form)
        generator_data = await uploaded_bytes(
            form.get("generator_file"), settings.source_limit_bytes
        )
        if generator_data is None:
            raise ValueError("Select a C++20 generator file.")
        try:
            case_count = int(str(form.get("case_count", "1")))
            base_seed = int(str(form.get("base_seed", "1")))
        except ValueError as exc:
            raise ValueError("Case count and base seed must be integers.") from exc
        build = queue_generator_build(
            db,
            problem,
            admin,
            generator_data,
            case_count,
            base_seed,
        )
        _flash(request, f"Generator build #{build.id} queued for the Worker.")
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        _flash(request, str(exc), "error")
    finally:
        await form.close()
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcases")
async def web_add_testcase(
    problem_id: str, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        form = await testcase_form(
            request,
            settings.testcase_file_limit_bytes,
            body_limit_bytes=6 * settings.testcase_file_limit_bytes + 128 * 1024,
        )
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _check_form_csrf(request, form)
        input_upload = await uploaded_bytes(
            form.get("input_file"), settings.testcase_file_limit_bytes
        )
        output_upload = await uploaded_bytes(
            form.get("output_file"), settings.testcase_file_limit_bytes
        )
        if (input_upload is None) != (output_upload is None):
            raise ValueError("Upload both input and expected-output files together.")
        add_testcase(
            db,
            problem,
            str(form.get("type", "hidden")),
            input_upload if input_upload is not None else str(form.get("input", "")),
            output_upload if output_upload is not None else str(form.get("output", "")),
            input_sha256=str(form.get("input_sha256", "")) or None,
            output_sha256=str(form.get("output_sha256", "")) or None,
        )
        _flash(request, "Testcase added.")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    finally:
        await form.close()
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcases/{testcase_id}/edit")
async def web_update_testcase(
    problem_id: str,
    testcase_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        form = await testcase_form(
            request,
            settings.testcase_file_limit_bytes,
            body_limit_bytes=6 * settings.testcase_file_limit_bytes + 128 * 1024,
        )
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        _check_form_csrf(request, form)
        testcase = update_testcase(
            db,
            problem,
            testcase_id,
            str(form.get("type", "hidden")),
            input_data=await uploaded_bytes(
                form.get("input_file"), settings.testcase_file_limit_bytes
            ),
            output_data=await uploaded_bytes(
                form.get("output_file"), settings.testcase_file_limit_bytes
            ),
            input_sha256=str(form.get("input_sha256", "")) or None,
            output_sha256=str(form.get("output_sha256", "")) or None,
        )
        _flash(request, f"Testcase #{testcase.order} updated.")
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        _flash(request, str(exc), "error")
    finally:
        await form.close()
    return _redirect(request, f"/admin/problems/{problem_id}/edit")


@router.post("/admin/problems/{problem_id}/testcases/{testcase_id}/delete")
async def web_delete_testcase(
    problem_id: str,
    testcase_id: int,
    request: Request,
    db: Session = Depends(get_db),
) -> RedirectResponse:
    admin = _require_admin(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    problem = db.get(Problem, problem_id)
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    form = await request.form()
    _check_form_csrf(request, form)
    try:
        testcase = delete_testcase(db, problem, testcase_id)
        _flash(request, f"Testcase #{testcase.order} deleted.")
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    if problem is None or problem.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Problem not found")
    try:
        delete_problem(db, problem)
        _flash(request, "Problem deleted.")
    except ValueError as exc:
        _flash(request, str(exc), "error")
    return _redirect(request, "/admin")


@router.post("/admin/users/{user_id}")
async def update_user(
    user_id: int, request: Request, db: Session = Depends(get_db)
) -> RedirectResponse:
    admin = _require_system(request, db)
    if isinstance(admin, RedirectResponse):
        return admin
    form = await request.form()
    _check_form_csrf(request, form)
    role = str(form.get("role", "user"))
    active = form.get("is_active") == "on"
    from minioj.accounts import update_user_access

    update_user_access(db, admin, user_id, role, active)
    _flash(request, "User access updated.")
    return _redirect(request, "/manage/users")


# Keep old editing endpoints while using the same implementations in the Console.
for legacy_route in list(router.routes):
    if legacy_route.path.startswith("/admin/problems/"):
        router.add_api_route(
            "/manage" + legacy_route.path[len("/admin") :],
            legacy_route.endpoint,
            methods=list(legacy_route.methods),
            response_class=legacy_route.response_class,
            name="manage_" + legacy_route.name,
            include_in_schema=False,
        )
