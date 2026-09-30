from __future__ import annotations

import asyncio
import re
from unittest.mock import Mock

import pytest

from minioj.accounts import create_administrator
from minioj.database import SessionLocal
from minioj.models import User
from minioj.security import (
    hash_password,
    valid_email,
    validate_password,
    verify_password,
)
from minioj.server.main import app
from minioj.server.middleware import ACCOUNT_BODY_MAX_BYTES, AccountBodyLimitMiddleware

PASSWORD = "long-enough-password"


def csrf(client, path="/register"):
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match
    return match.group(1)


def registration(**overrides):
    return {
        "username": "Alice",
        "email": "alice@example.com",
        "password": PASSWORD,
        "password_confirmation": PASSWORD,
        **overrides,
    }


@pytest.mark.parametrize(
    "username",
    ["ab", "abcdefghijk", "alice1", "a_b", "a.b", "a-b", "中文名", "élise", "a'--"],
)
def test_username_rules_on_all_creation_paths(client, username):
    data = registration(username=username)
    assert client.post("/api/v1/auth/register", json=data).status_code == 422
    assert (
        client.post("/register", data={**data, "csrf_token": csrf(client)}).status_code
        == 422
    )
    with pytest.raises(ValueError, match="English letters"):
        create_administrator(username, data["email"], PASSWORD)
    with SessionLocal() as db:
        assert db.query(User).count() == 0


@pytest.mark.parametrize("username", ["Abc", "Abcdefghij"])
@pytest.mark.parametrize("api", [False, True])
def test_username_boundaries_and_normalization(client, username, api):
    data = registration(username=f" {username} ", email=" ALICE+tag@EXAMPLE.COM ")
    if api:
        response = client.post("/api/v1/auth/register", json=data)
        assert response.status_code == 201
    else:
        response = client.post(
            "/register",
            data={**data, "csrf_token": csrf(client)},
            follow_redirects=False,
        )
        assert response.status_code == 303
    with SessionLocal() as db:
        user = db.query(User).one()
        assert user.username == username
        assert user.email == "alice+tag@example.com"


@pytest.mark.parametrize(
    "email",
    [
        "a@@example.com",
        "@example.com",
        "a@",
        "a@example",
        "a..b@example.com",
        "a b@example.com",
        "a\r\n@example.com",
        "<script>@example.com",
        '"a"@example.com',
        "a@-example.com",
        "a@example..com",
        "a@" + "b" * 64 + ".com",
        "a" * 65 + "@example.com",
        "a@" + ".".join(["b" * 63] * 4),
    ],
)
def test_email_rejected_on_web_api_and_admin(client, email):
    data = registration(email=email)
    assert client.post("/api/v1/auth/register", json=data).status_code == 422
    response = client.post("/register", data={**data, "csrf_token": csrf(client)})
    assert response.status_code == 422
    assert "<script>" not in response.text
    with pytest.raises(ValueError, match="email"):
        create_administrator("Owner", email, PASSWORD)


def test_email_length_boundaries():
    email = "a" * 64 + "@" + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
    assert len(email) == 254
    assert valid_email(email)
    assert not valid_email(email + "d")
    assert valid_email("o'connor+tag@example.com")


@pytest.mark.parametrize("password", ["x" * 9, "x" * 1025, "中" * 342, "\ud800" * 10])
def test_invalid_passwords_never_reach_hasher(monkeypatch, password):
    hasher = Mock()
    monkeypatch.setattr("minioj.security.password_hasher", hasher)
    assert validate_password(password)
    with pytest.raises(ValueError):
        hash_password(password)
    if len(password) >= 10:
        assert not verify_password(password, "unused-hash")
    hasher.hash.assert_not_called()
    hasher.verify.assert_not_called()


@pytest.mark.parametrize(
    "password", ["x" * 1024, "中" * 341 + "x", " ' OR 1=1; <script> "]
)
def test_passwords_preserve_symbols_whitespace_and_byte_boundary(client, password):
    data = registration(password=password, password_confirmation=password)
    assert client.post("/api/v1/auth/register", json=data).status_code == 201
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client, "/login"),
            "identity": "Alice",
            "password": password,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


@pytest.mark.parametrize("field", ["password", "password_confirmation"])
def test_oversized_registration_password_rejected_before_hashing(
    client, monkeypatch, field
):
    hasher = Mock()
    monkeypatch.setattr("minioj.security.password_hasher", hasher)
    data = registration(**{field: "中" * 342})
    assert client.post("/api/v1/auth/register", json=data).status_code == 422
    assert (
        client.post("/register", data={**data, "csrf_token": csrf(client)}).status_code
        == 422
    )
    hasher.hash.assert_not_called()


def test_login_rejects_oversized_inputs_and_sql_injection(client, monkeypatch):
    create_administrator("Owner", "owner@example.com", PASSWORD)
    hasher = Mock()
    monkeypatch.setattr("minioj.security.password_hasher", hasher)
    token = csrf(client, "/login")
    for identity, password in [
        ("Owner", "中" * 342),
        ("x" * 255, PASSWORD),
        ("' OR 1=1 --", PASSWORD),
        ("<script>alert(1)</script>", PASSWORD),
    ]:
        response = client.post(
            "/login",
            data={"csrf_token": token, "identity": identity, "password": password},
        )
        assert response.status_code == 401
        assert "<script>" not in response.text
    hasher.verify.assert_not_called()
    with SessionLocal() as db:
        assert db.query(User).count() == 1


def test_profile_and_password_validation_preserve_account(client, monkeypatch):
    client.post("/register", data={**registration(), "csrf_token": csrf(client)})
    token = csrf(client, "/settings")
    response = client.post(
        "/settings/profile", data={"csrf_token": token, "email": "<script>@example.com"}
    )
    assert "valid email" in response.text
    hasher = Mock()
    monkeypatch.setattr("minioj.security.password_hasher", hasher)
    for field in ["current_password", "password", "password_confirmation"]:
        data = {
            "csrf_token": token,
            "current_password": PASSWORD,
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
            field: "中" * 342,
        }
        response = client.post("/settings/password", data=data)
        assert "Password changed." not in response.text
    hasher.verify.assert_not_called()
    hasher.hash.assert_not_called()
    with SessionLocal() as db:
        assert db.query(User).one().email == "alice@example.com"


def test_old_username_can_still_log_in(client):
    with SessionLocal() as db:
        db.add(
            User(
                username="old_user123",
                email="old@example.com",
                password_hash=hash_password(PASSWORD),
                role="user",
            )
        )
        db.commit()
    response = client.post(
        "/login",
        data={
            "csrf_token": csrf(client, "/login"),
            "identity": "old_user123",
            "password": PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_token_copy_shown_once_and_name_escaped_under_subpath(client):
    original = app.root_path
    app.root_path = "/minioj"
    try:
        client.post("/register", data={**registration(), "csrf_token": csrf(client)})
        response = client.post(
            "/settings/tokens",
            data={
                "csrf_token": csrf(client, "/settings"),
                "name": "<script>alert(1)</script>",
            },
        )
        assert response.status_code == 200
        assert 'id="copy-api-token"' in response.text
        assert 'src="http://testserver/minioj/static/settings.js"' in response.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
        match = re.search(
            r'<code id="new-api-token"[^>]*>(oj_[^<]+)</code>', response.text
        )
        assert match
        next_page = client.get("/settings")
        assert match.group(1) not in next_page.text
        assert 'id="copy-api-token"' not in next_page.text
    finally:
        app.root_path = original


@pytest.mark.parametrize(
    "path",
    [
        "/register",
        "/register/",
        "/login",
        "/settings/profile",
        "/settings/password",
        "/api/v1/auth/register",
    ],
)
def test_large_account_body_rejected_before_parsing(client, path):
    response = client.post(path, content=b"x" * (ACCOUNT_BODY_MAX_BYTES + 1))
    assert response.status_code == 413


@pytest.mark.parametrize("root", ["", "/minioj"])
@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_chunked_body_limit_without_trusting_content_length(root, headers):
    called = False
    sent = []
    chunks = iter(
        [
            {
                "type": "http.request",
                "body": b"x" * ACCOUNT_BODY_MAX_BYTES,
                "more_body": True,
            },
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    asyncio.run(
        AccountBodyLimitMiddleware(downstream)(
            {
                "type": "http",
                "method": "POST",
                "path": root + "/api/v1/auth/register",
                "root_path": root,
                "headers": headers,
            },
            receive,
            send,
        )
    )
    assert not called
    assert sent[0]["status"] == 413


def test_account_body_limit_does_not_limit_source_submissions(client):
    response = client.post(
        "/api/v1/submissions",
        json={
            "problem_id": "example",
            "source_code": "x" * (ACCOUNT_BODY_MAX_BYTES + 1),
        },
    )
    assert response.status_code == 401
