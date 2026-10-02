from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from minioj.database import SessionLocal, init_db
from minioj.models import User
from minioj.permissions import ROLE_PERMISSIONS, Permission, require_permission
from minioj.security import USERNAME_RE, hash_password, valid_email, validate_password


def create_administrator(
    username: str, email: str, password: str, *, allow_existing: bool = False
) -> None:
    """Create a system administrator through the compatible bootstrap command."""
    username = username.strip()
    email = email.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Username must contain 3-10 English letters (A-Z or a-z).")
    if not valid_email(email):
        raise ValueError("Invalid email.")
    if error := validate_password(password):
        raise ValueError(error)
    init_db()
    with SessionLocal() as db:
        existing = db.scalars(
            select(User).where(
                or_(func.lower(User.username) == username.lower(), User.email == email)
            )
        ).all()
        if existing:
            if (
                allow_existing
                and len(existing) == 1
                and existing[0].username.lower() == username.lower()
                and existing[0].email == email
                and existing[0].role == "system"
                and existing[0].is_active
            ):
                return
            raise ValueError(
                "Username or email already exists. No permissions were changed. "
                "Choose an unused administrator username and email."
            )
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                role="system",
            )
        )
        db.commit()


def update_user_access(
    db: Session, actor: User, user_id: int, role: str, active: bool
) -> None:
    require_permission(actor, Permission.USERS)
    if role not in ROLE_PERMISSIONS:
        raise HTTPException(status_code=422, detail="Invalid role")
    locked = db.execute(update(User).where(User.id == user_id).values(role=User.role))
    if locked.rowcount != 1:
        raise HTTPException(status_code=404, detail="User not found")
    user = db.get(User, user_id)
    db.refresh(user)
    if user.id == actor.id and (role != "system" or not active):
        raise HTTPException(
            status_code=409,
            detail="You cannot demote or deactivate your own system account",
        )
    if user.role == "system" and user.is_active and (role != "system" or not active):
        count = db.scalar(
            select(func.count(User.id)).where(
                User.role == "system", User.is_active.is_(True)
            )
        )
        if count <= 1:
            raise HTTPException(
                status_code=409,
                detail="The last active system account must be preserved",
            )
    user.role, user.is_active = role, active
    db.commit()
