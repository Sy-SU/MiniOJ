from enum import StrEnum

from fastapi import HTTPException


class Permission(StrEnum):
    CONTENT = "content"
    SUBMISSIONS = "submissions"
    REJUDGE = "rejudge"
    CONTESTS = "contests"
    USERS = "users"
    SYSTEM = "system"


ROLE_PERMISSIONS = {
    "user": frozenset(),
    "admin": frozenset(
        {
            Permission.CONTENT,
            Permission.SUBMISSIONS,
            Permission.REJUDGE,
            Permission.CONTESTS,
        }
    ),
    "system": frozenset(Permission),
}


def can(user, permission: Permission) -> bool:
    return (
        user is not None
        and user.is_active
        and permission in ROLE_PERMISSIONS.get(user.role, ())
    )


def require_permission(user, permission: Permission):
    if not can(user, permission):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user
