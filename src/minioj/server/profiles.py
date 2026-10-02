from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from minioj.avatars import AVATAR_LIMIT_BYTES, avatar_path, save_avatar
from minioj.config import settings
from minioj.database import get_db
from minioj.models import Submission, User
from minioj.profiles import profile_statistics
from minioj.server.uploads import TestcaseFileTooLarge, testcase_form, uploaded_bytes
from minioj.server.web import (
    _check_form_csrf,
    _context,
    _flash,
    _redirect,
    _require_user,
    templates,
)

router = APIRouter(include_in_schema=False)


def find_user(db, username):
    user = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users/{username}")
def profile(username: str, request: Request, db: Session = Depends(get_db)):
    user = find_user(db, username)
    recent = db.scalars(
        select(Submission)
        .where(Submission.user_id == user.id)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(20)
    ).all()
    return templates.TemplateResponse(
        request=request,
        name="profile.html",
        context=_context(
            request,
            db,
            profile_user=user,
            stats=profile_statistics(db, user.id),
            recent=recent,
        ),
    )


@router.get("/users/{username}/avatar")
def avatar(username: str, db: Session = Depends(get_db)):
    user = find_user(db, username)
    if user.avatar_key:
        try:
            path = avatar_path(user.avatar_key)
            if path.is_file():
                return FileResponse(
                    path,
                    media_type="image/png",
                    headers={
                        "X-Content-Type-Options": "nosniff",
                        "Cache-Control": "no-cache",
                    },
                )
        except ValueError:
            pass
    return FileResponse(
        settings.static_dir / "default-avatar.svg",
        media_type="image/svg+xml",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"},
    )


@router.post("/settings/avatar")
async def update_avatar(request: Request, db: Session = Depends(get_db)):
    user = _require_user(request, db)
    if isinstance(user, RedirectResponse):
        return user
    key = None
    form = None
    try:
        form = await testcase_form(
            request, AVATAR_LIMIT_BYTES, body_limit_bytes=AVATAR_LIMIT_BYTES + 64 * 1024
        )
        _check_form_csrf(request, form)
        value = form.get("avatar")
        if not isinstance(value, UploadFile) or not value.filename:
            raise HTTPException(status_code=422, detail="Choose an avatar file")
        data = await uploaded_bytes(value, AVATAR_LIMIT_BYTES)
        try:
            key = save_avatar(value.filename, value.content_type, data)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.execute(
            update(User).where(User.id == user.id).values(avatar_key=User.avatar_key)
        )
        db.refresh(user)
        old_key = user.avatar_key
        user.avatar_key = key
        db.commit()
    except TestcaseFileTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except MultiPartException as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except BaseException:
        db.rollback()
        if key:
            avatar_path(key).unlink(missing_ok=True)
        raise
    finally:
        if form is not None:
            await form.close()
    if old_key:
        try:
            avatar_path(old_key).unlink(missing_ok=True)
        except (ValueError, OSError):
            pass
    _flash(request, "Avatar updated.")
    return _redirect(request, "/settings")
