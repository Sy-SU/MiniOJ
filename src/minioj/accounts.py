from __future__ import annotations

from sqlalchemy import func, or_, select

from minioj.database import SessionLocal, init_db
from minioj.models import User
from minioj.security import USERNAME_RE, hash_password, valid_email, validate_password


def create_administrator(
    username: str, email: str, password: str, *, allow_existing: bool = False
) -> None:
    """Create an admin, or verify an existing bootstrap admin without changing it."""
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
                and existing[0].role == "admin"
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
                role="admin",
            )
        )
        db.commit()
