import re
from datetime import UTC, datetime, timedelta

import pytest

from minioj.database import SessionLocal
from minioj.models import ApiToken, User
from minioj.security import create_api_token, hash_password

PASSWORD = "long-enough-password"


def make_token(username="Alice", state="active"):
    token_id, raw, digest, expires = create_api_token()
    with SessionLocal() as db:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        db.flush()
        db.add(
            ApiToken(
                id=token_id,
                user_id=user.id,
                name="test token",
                token_hash=digest,
                expires_at=datetime.now(UTC) - timedelta(days=1)
                if state == "expired"
                else expires,
                revoked_at=datetime.now(UTC) if state == "revoked" else None,
            )
        )
        db.commit()
    return token_id, raw


def login(client):
    token = re.search(
        r'name="csrf_token" value="([^"]+)"', client.get("/login").text
    ).group(1)
    client.post(
        "/login", data={"csrf_token": token, "identity": "Alice", "password": PASSWORD}
    )
    page = client.get("/settings")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    return csrf, page.text


def delete(client, token_id, csrf, api):
    if api:
        return client.delete(
            f"/api/v1/tokens/{token_id}", headers={"X-CSRF-Token": csrf}
        )
    return client.post(
        f"/settings/tokens/{token_id}/delete",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )


@pytest.mark.parametrize("api", [False, True])
@pytest.mark.parametrize("state", ["active", "revoked", "expired"])
def test_delete_removes_token_and_invalidates_secret(client, api, state):
    token_id, raw = make_token(state=state)
    csrf, page = login(client)
    assert f'/settings/tokens/{token_id}/delete"' in page
    assert ">Delete</button>" in page
    assert delete(client, token_id, csrf, api).status_code == (204 if api else 303)
    with SessionLocal() as db:
        assert db.get(ApiToken, token_id) is None
    assert token_id not in client.get("/settings").text
    assert client.get("/api/v1/tokens").json() == []
    assert (
        client.get(
            "/api/v1/tokens", headers={"Authorization": f"Bearer {raw}"}
        ).status_code
        == 401
    )
    assert delete(client, token_id, csrf, api).status_code == 404


@pytest.mark.parametrize("api", [False, True])
def test_token_deletion_requires_owner_and_csrf(client, api):
    own_id, _ = make_token()
    other_id, _ = make_token("Bob")
    csrf, _ = login(client)
    assert delete(client, own_id, "invalid", api).status_code == 403
    assert delete(client, other_id, csrf, api).status_code == 404
    with SessionLocal() as db:
        assert db.get(ApiToken, own_id) is not None
        assert db.get(ApiToken, other_id) is not None


def test_bearer_token_can_delete_itself(client):
    token_id, raw = make_token()
    headers = {"Authorization": f"Bearer {raw}"}
    assert (
        client.delete(f"/api/v1/tokens/{token_id}", headers=headers).status_code == 204
    )
    assert client.get("/api/v1/tokens", headers=headers).status_code == 401
