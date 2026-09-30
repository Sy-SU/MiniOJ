from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

password_hasher = PasswordHasher()
USERNAME_MAX_LENGTH = 10
EMAIL_MAX_LENGTH = 254
PASSWORD_MAX_BYTES = 1024
EMAIL_LOCAL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
)
EMAIL_LABEL_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
USERNAME_RE = re.compile(r"^[A-Za-z]{3,10}$")
PROBLEM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$")


def is_reserved_username(username: str) -> bool:
    reserved = {"admin", os.getenv("MINIOJ_ADMIN_USERNAME", "").strip().casefold()}
    return username.strip().casefold() in reserved


def hash_password(password: str) -> str:
    if error := validate_password(password):
        raise ValueError(error)
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    if not password_within_limit(password):
        return False
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def password_within_limit(password: str) -> bool:
    if len(password) > PASSWORD_MAX_BYTES:
        return False
    try:
        return len(password.encode("utf-8")) <= PASSWORD_MAX_BYTES
    except UnicodeEncodeError:
        return False


def valid_email(email: str) -> bool:
    """Accept ASCII dot-atom addresses with a dotted DNS domain; no display names."""
    if len(email) > EMAIL_MAX_LENGTH or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    return bool(
        len(local) <= 64
        and EMAIL_LOCAL_RE.fullmatch(local)
        and "." in domain
        and all(EMAIL_LABEL_RE.fullmatch(label) for label in domain.split("."))
    )


def validate_password(password: str) -> str | None:
    if len(password) < 10:
        return "Password must contain at least 10 characters."
    if not password_within_limit(password):
        return "Password must be valid UTF-8 and at most 1024 bytes."
    return None


def create_api_token(days: int = 90) -> tuple[str, str, str, datetime]:
    raw = "oj_" + secrets.token_urlsafe(32)
    token_id = "token_" + secrets.token_urlsafe(12)
    expires_at = datetime.now(UTC) + timedelta(days=days)
    return token_id, raw, hash_token(raw), expires_at


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_submission_id() -> str:
    return "sub_" + secrets.token_urlsafe(12)


def csrf_token(session: dict) -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return str(session["csrf_token"])


def valid_csrf(session: dict, supplied: str | None) -> bool:
    expected = session.get("csrf_token")
    return bool(
        expected and supplied and secrets.compare_digest(str(expected), supplied)
    )
