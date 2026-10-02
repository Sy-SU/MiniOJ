from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from minioj.database import get_db
from minioj.models import ApiToken, User
from minioj.permissions import Permission, require_permission
from minioj.security import hash_token, valid_csrf


def _bearer_value(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value


def user_from_bearer(db: Session, authorization: str | None) -> User | None:
    raw = _bearer_value(authorization)
    if raw is None or not raw.startswith("oj_"):
        return None
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(raw)))
    now = datetime.now(UTC)
    if token is None or token.revoked_at is not None:
        return None
    expires_at = token.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            return None
    if not token.user.is_active:
        return None
    token.last_used_at = now
    db.commit()
    return token.user


def optional_user(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    if authorization:
        return user_from_bearer(db, authorization)
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    return user if user and user.is_active else None


def current_user(request: Request, user: User | None = Depends(optional_user)) -> User:
    if user is None:
        headers = (
            {"WWW-Authenticate": "Bearer"}
            if request.headers.get("Authorization")
            else None
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers=headers,
        )
    return user


def bearer_user(
    authorization: str | None = Header(default=None), db: Session = Depends(get_db)
) -> User:
    user = user_from_bearer(db, authorization)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    return require_permission(user, Permission.CONTENT)


def system_user(user: User = Depends(current_user)) -> User:
    return require_permission(user, Permission.SYSTEM)


def require_session_csrf(request: Request, authorization: str | None) -> None:
    """Bearer requests are CSRF-safe; cookie-authenticated mutations need a header."""
    if _bearer_value(authorization):
        return
    if not valid_csrf(request.session, request.headers.get("X-CSRF-Token")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token"
        )
