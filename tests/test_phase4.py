from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace

import pytest

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.models import ApiToken, Problem, User
from minioj.security import create_api_token, hash_password
from minioj.server.middleware import AccountBodyLimitMiddleware, _api_body_limit

PASSWORD = "phase-four-test-password"


def seed_user(name: str, *, active: bool = True, role: str = "user") -> tuple[int, str]:
    token_id, raw, digest, expires_at = create_api_token()
    with SessionLocal() as db:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash=hash_password(PASSWORD),
            is_active=active,
            role=role,
        )
        db.add(user)
        db.flush()
        db.add(
            ApiToken(
                id=token_id,
                user_id=user.id,
                name="phase-four",
                token_hash=digest,
                expires_at=expires_at,
            )
        )
        db.commit()
        return user.id, raw


def seed_problem(owner_id: int) -> None:
    with SessionLocal() as db:
        db.add(
            Problem(
                id="phase-four",
                title="Phase Four",
                statement="Add two numbers.",
                input_specification="Two integers.",
                output_specification="One integer.",
                notes="Machine API test.",
                created_by=owner_id,
            )
        )
        db.commit()


def bearer(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def login(client, name: str) -> str:
    page = client.get("/login")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    response = client.post(
        "/login",
        data={"csrf_token": csrf, "identity": name, "password": PASSWORD},
    )
    assert response.status_code == 200
    settings_page = client.get("/settings")
    return re.search(r'name="csrf_token" value="([^"]+)"', settings_page.text).group(1)


@pytest.mark.parametrize("feedback_mode", ["full", "diagnostic", "verdict_only"])
def test_machine_api_response_contract_and_pending_nulls(
    client, monkeypatch, feedback_mode
):
    monkeypatch.setattr(
        "minioj.server.api.settings", replace(settings, feedback_policy=feedback_mode)
    )
    user_id, raw = seed_user("ContractUser")
    seed_problem(user_id)
    headers = bearer(raw)

    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert set(me.json()) == {
        "id",
        "username",
        "email",
        "role",
        "is_active",
        "created_at",
        "feedback_mode",
    }
    assert me.json()["feedback_mode"] == feedback_mode
    login(client, "ContractUser")
    assert client.get("/api/v1/me").json() == me.json()

    problems = client.get("/api/v1/problems").json()
    assert set(problems[0]) == {
        "problem_id",
        "title",
        "source",
        "source_id",
        "rating",
        "tags",
        "limits",
    }
    agent_problem = client.get(
        "/api/v1/agent/problems/phase-four", headers=headers
    ).json()
    assert set(agent_problem) == {
        "problem_id",
        "title",
        "statement",
        "input_specification",
        "output_specification",
        "notes",
        "limits",
        "samples",
    }

    created = client.post(
        "/api/v1/submissions",
        headers=headers,
        json={"problem_id": "phase-four", "source_code": "int main(){}"},
    )
    assert created.status_code == 202
    assert created.json() == {"submission_id": 1, "status": "QUEUED"}
    pending = client.get("/api/v1/submissions/1", headers=headers)
    assert pending.status_code == 200
    assert pending.json() == {
        "submission_id": 1,
        "problem_id": "phase-four",
        "language": "cpp20",
        "status": "QUEUED",
        "verdict": None,
        "tests": None,
        "resources": None,
        "created_at": pending.json()["created_at"],
        "started_at": None,
        "finished_at": None,
    }


@pytest.mark.parametrize(
    ("path", "method", "expected"),
    [
        ("/api/v1/me", "get", 401),
        ("/api/v1/problems/missing", "get", 404),
        ("/api/v1/runs", "post", 422),
    ],
)
def test_api_errors_have_stable_envelope(client, path, method, expected):
    headers = None
    payload = None
    if path == "/api/v1/runs":
        _, raw = seed_user("ErrorUser")
        headers = bearer(raw)
        payload = {"source_code": "DO-NOT-ECHO", "language": "invalid"}
    if method == "post":
        response = client.post(path, headers=headers, json=payload)
    else:
        response = client.get(path, headers=headers)
    assert response.status_code == expected
    body = response.json()
    assert set(body) == {"error", "detail"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] in {
        "authentication_required",
        "not_found",
        "validation_error",
    }
    assert "DO-NOT-ECHO" not in response.text


def test_api_validation_does_not_echo_password_or_token_input(client):
    marker = "SECRET-MARKER-SHOULD-NOT-BE-ECHOED"
    oversized_password = marker * 40
    registration = client.post(
        "/api/v1/auth/register",
        json={
            "username": "bad",
            "email": "not-an-email",
            "password": oversized_password,
            "password_confirmation": oversized_password,
        },
    )
    assert registration.status_code == 422
    assert marker not in registration.text

    _, raw = seed_user("WhitespaceToken")
    response = client.post("/api/v1/tokens", headers=bearer(raw), json={"name": "   "})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_session_mutation_requires_csrf_but_bearer_does_not(client):
    _, raw = seed_user("CsrfUser")
    login(client, "CsrfUser")
    denied = client.post("/api/v1/tokens", json={"name": "session token"})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    allowed = client.post(
        "/api/v1/tokens", headers=bearer(raw), json={"name": "bearer token"}
    )
    assert allowed.status_code == 201


def test_disabled_user_token_is_rejected(client):
    _, raw = seed_user("DisabledUser", active=False)
    response = client.get("/api/v1/me", headers=bearer(raw))
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/submissions"),
        ("POST", "/api/v1/runs"),
        ("POST", "/api/v1/admin/problems/abc/testcases"),
        ("PUT", "/api/v1/admin/problems/abc/testcases/1"),
    ],
)
@pytest.mark.parametrize("root_path", ["", "/minioj"])
def test_api_body_limit_rejects_chunked_input_before_parsing(
    monkeypatch, method, path, root_path
):
    from minioj.server import middleware

    monkeypatch.setattr(
        middleware,
        "settings",
        replace(
            settings,
            source_limit_bytes=4,
            stdin_limit_bytes=4,
            testcase_file_limit_bytes=4,
        ),
    )
    monkeypatch.setattr(middleware, "JSON_BODY_OVERHEAD_BYTES", 4)
    monkeypatch.setattr(middleware, "JSON_ESCAPE_EXPANSION", 1)
    called = False
    sent = []
    chunks = iter(
        [
            {"type": "http.request", "body": b"x" * 12, "more_body": True},
            {"type": "http.request", "body": b"x" * 20, "more_body": False},
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
                "method": method,
                "path": root_path + path,
                "root_path": root_path,
                "headers": [],
            },
            receive,
            send,
        )
    )
    assert not called
    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload["error"]["code"] == "payload_too_large"


def test_custom_run_body_budget_includes_both_compatible_source_fields(monkeypatch):
    from minioj.server import middleware

    limited = replace(settings, source_limit_bytes=10, stdin_limit_bytes=5)
    monkeypatch.setattr(middleware, "settings", limited)
    assert _api_body_limit("/api/v1/runs", "POST") == (
        middleware.JSON_ESCAPE_EXPANSION * 25 + middleware.JSON_BODY_OVERHEAD_BYTES
    )


def test_source_and_stdin_byte_limits_handle_multibyte_text(client, monkeypatch):
    _, raw = seed_user("ByteLimitUser")
    limited = replace(settings, source_limit_bytes=4, stdin_limit_bytes=4)
    monkeypatch.setattr("minioj.server.api.settings", limited)
    monkeypatch.setattr("minioj.server.middleware.settings", limited)
    for payload in (
        {"source_code": "ééé"},
        {"source_code": "x", "stdin": "界界"},
    ):
        response = client.post("/api/v1/runs", headers=bearer(raw), json=payload)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"


def test_web_testcase_upload_body_is_limited_before_multipart_parsing(
    client, monkeypatch
):
    admin_id, _ = seed_user("UploadAdmin", role="admin")
    seed_problem(admin_id)
    csrf = login(client, "UploadAdmin")
    limited = replace(settings, testcase_file_limit_bytes=4)
    monkeypatch.setattr("minioj.server.web.settings", limited)
    response = client.post(
        "/admin/problems/phase-four/testcases",
        data={"csrf_token": csrf, "type": "hidden", "padding": "x" * 132_000},
        files={
            "input_file": ("case.in", b"1", "text/plain"),
            "output_file": ("case.out", b"1", "text/plain"),
        },
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "Upload request is too large."


def test_openapi_references_response_and_error_models(client):
    schema = client.get("/openapi.json").json()
    me = schema["components"]["schemas"]["CurrentUserResponse"]
    assert "feedback_mode" in me["required"]
    assert me["properties"]["feedback_mode"]["enum"] == [
        "full",
        "diagnostic",
        "verdict_only",
    ]
    operation = schema["paths"]["/api/v1/submissions/{submission_id}"]["get"]
    success = operation["responses"]["200"]["content"]["application/json"]["schema"]
    error = operation["responses"]["404"]["content"]["application/json"]["schema"]
    assert success["$ref"].endswith("/SubmissionDetailResponse")
    assert error["$ref"].endswith("/ErrorResponse")
    required = set(
        schema["components"]["schemas"]["SubmissionDetailResponse"]["required"]
    )
    assert {"verdict", "tests", "resources", "started_at", "finished_at"} <= required
