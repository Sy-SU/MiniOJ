import re

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from minioj.database import SessionLocal, engine, init_db
from minioj.models import User
from minioj.security import hash_password

PASSWORD = "case-test-password"


def csrf(client, path):
    return re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text).group(
        1
    )


def add_user(name="YeungSusan", *, active=True):
    with SessionLocal() as db:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash=hash_password(PASSWORD),
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user.id


@pytest.mark.parametrize(
    "identity", ["YeungSusan", "yeungsusan", "YEUNGSUSAN", " yEuNgSuSaN "]
)
def test_login_resolves_same_user_and_preserves_display_name(client, identity):
    user_id = add_user()
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client, "/login"),
            "identity": identity,
            "password": PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    me = client.get("/api/v1/me").json()
    assert me["id"] == user_id
    assert me["username"] == "YeungSusan"


@pytest.mark.parametrize("api", [True, False])
@pytest.mark.parametrize("name", ["yeungsusan", "YEUNGSUSAN"])
def test_registration_rejects_case_variant(client, api, name):
    user_id = add_user()
    payload = {
        "username": name,
        "email": "different@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
    }
    if api:
        response = client.post("/api/v1/auth/register", json=payload)
    else:
        payload["csrf_token"] = csrf(client, "/register")
        response = client.post("/register", data=payload)
    assert response.status_code == 409
    with SessionLocal() as db:
        users = db.scalars(select(User)).all()
        assert len(users) == 1
        assert users[0].id == user_id


@pytest.mark.parametrize(
    "active,password", [(False, PASSWORD), (True, "wrong-password")]
)
def test_case_insensitive_login_still_checks_password_and_active(
    client, active, password
):
    add_user(active=active)
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client, "/login"),
            "identity": "yeungsusan",
            "password": password,
        },
    )
    assert response.status_code == 401
    assert client.get("/api/v1/me").status_code == 401


def test_database_rejects_case_variant_without_application_precheck():
    add_user()
    with pytest.raises(IntegrityError):
        add_user("yeungsusan")


def test_existing_database_migration_preserves_account_and_is_idempotent():
    user_id = add_user()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_users_username_lower")
    init_db()
    init_db()
    with SessionLocal() as db:
        assert db.get(User, user_id).username == "YeungSusan"
    with pytest.raises(IntegrityError):
        add_user("yeungsusan")


def test_migration_reports_duplicates_without_merging_accounts():
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP INDEX uq_users_username_lower")
    first_id = add_user()
    second_id = add_user("yeungsusan")
    with pytest.raises(
        RuntimeError, match="Case-insensitive username conflicts: yeungsusan"
    ):
        init_db()
    with SessionLocal() as db:
        assert db.get(User, first_id).username == "YeungSusan"
        assert db.get(User, second_id).username == "yeungsusan"
