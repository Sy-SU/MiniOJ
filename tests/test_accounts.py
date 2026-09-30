from __future__ import annotations

import re

import pytest

from minioj.cli import create_admin
from minioj.database import SessionLocal
from minioj.models import User
from minioj.security import hash_password, verify_password
from minioj.server.main import _bootstrap_admin

PASSWORD = "test-administrator-password"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


@pytest.mark.parametrize("username", ["admin", "ADMIN", "AdMiN", "site_owner"])
def test_reserved_names_rejected_by_web_and_api(client, monkeypatch, username):
    monkeypatch.setenv("MINIOJ_ADMIN_USERNAME", "site_owner")
    data = {
        "username": username,
        "email": "test@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
    }
    api = client.post("/api/v1/auth/register", json=data)
    assert api.status_code == 422
    assert "reserved" in api.json()["detail"]
    data["csrf_token"] = csrf(client.get("/register").text)
    web = client.post("/register", data=data)
    assert web.status_code == 422
    assert "reserved" in web.text
    with SessionLocal() as db:
        assert db.query(User).count() == 0


def test_first_public_user_cannot_request_admin_role(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": "ordinary_user",
            "email": "ordinary@example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            "role": "admin",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "user"
    page = client.get("/login")
    client.post(
        "/login",
        data={
            "csrf_token": csrf(page.text),
            "identity": "ordinary_user",
            "password": PASSWORD,
        },
    )
    assert client.get("/admin").status_code == 403


def test_cli_admin_can_sign_in_and_access_admin_page(client, capsys):
    create_admin(" admin ", " OWNER@EXAMPLE.COM ", PASSWORD)
    output = capsys.readouterr().out
    assert "role=admin" in output
    assert "Database:" in output
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(page.text),
            "identity": "admin",
            "password": PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert client.get("/admin").status_code == 200
    with SessionLocal() as db:
        user = db.query(User).one()
        assert user.role == "admin"
        assert user.email == "owner@example.com"


def bootstrap_env(monkeypatch):
    monkeypatch.setenv("MINIOJ_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MINIOJ_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("MINIOJ_ADMIN_PASSWORD", PASSWORD)


def test_bootstrap_is_idempotent_and_preserves_password(monkeypatch):
    bootstrap_env(monkeypatch)
    _bootstrap_admin()
    monkeypatch.setenv("MINIOJ_ADMIN_PASSWORD", "different-long-password")
    _bootstrap_admin()
    with SessionLocal() as db:
        user = db.query(User).one()
        assert user.role == "admin"
        assert verify_password(PASSWORD, user.password_hash)


@pytest.mark.parametrize(
    "username,email,role,active",
    [
        ("admin", "owner@example.com", "user", True),
        ("other", "owner@example.com", "admin", True),
        ("admin", "different@example.com", "admin", True),
        ("ADMIN", "owner@example.com", "user", True),
        ("admin", "owner@example.com", "admin", False),
    ],
)
def test_bootstrap_reports_conflicts_without_promoting_accounts(
    monkeypatch, username, email, role, active
):
    bootstrap_env(monkeypatch)
    with SessionLocal() as db:
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(PASSWORD),
                role=role,
                is_active=active,
            )
        )
        db.commit()
    with pytest.raises(RuntimeError, match="already exists"):
        _bootstrap_admin()
    with SessionLocal() as db:
        user = db.query(User).one()
        assert user.role == role
        assert user.is_active == active


def test_bootstrap_rejects_partial_configuration(monkeypatch):
    bootstrap_env(monkeypatch)
    monkeypatch.delenv("MINIOJ_ADMIN_PASSWORD")
    with pytest.raises(RuntimeError, match="Set all three"):
        _bootstrap_admin()


def test_cli_rejects_existing_name_case_insensitively():
    create_admin("admin", "owner@example.com", PASSWORD)
    with pytest.raises(SystemExit, match="already exists"):
        create_admin("ADMIN", "different@example.com", PASSWORD)
