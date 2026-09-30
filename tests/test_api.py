from __future__ import annotations

import json

from minioj.database import SessionLocal
from minioj.models import ApiToken, Problem, Submission, User
from minioj.security import create_api_token, hash_password


def add_user(username: str, role: str = "user") -> tuple[User, str]:
    token_id, raw, digest, expires = create_api_token()
    with SessionLocal() as db:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=hash_password("correct-horse-battery"),
            role=role,
        )
        db.add(user)
        db.flush()
        db.add(
            ApiToken(
                id=token_id,
                user_id=user.id,
                name="tests",
                token_hash=digest,
                expires_at=expires,
            )
        )
        db.commit()
        db.refresh(user)
        db.expunge(user)
    return user, raw


def add_problem(admin_id: int) -> Problem:
    with SessionLocal() as db:
        problem = Problem(
            id="sum-two",
            title="Sum Two Numbers",
            statement="Read two integers and print their sum.",
            input_specification="Two integers.",
            output_specification="Their sum.",
            notes="No tricks.",
            rating=800,
            tags="math, implementation",
            created_by=admin_id,
        )
        db.add(problem)
        db.commit()
        db.refresh(problem)
        db.expunge(problem)
    return problem


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_and_duplicate_conflict(client):
    payload = {
        "username": "newuser",
        "email": "new@example.com",
        "password": "long-enough-password",
        "password_confirmation": "long-enough-password",
    }
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_agent_problem_is_sanitized(client):
    admin, _ = add_user("admin", "admin")
    _, token = add_user("solver")
    add_problem(admin.id)
    response = client.get("/api/v1/agent/problems/sum-two", headers=auth(token))
    assert response.status_code == 200
    data = response.json()
    assert data["problem_id"] == "sum-two"
    assert "rating" not in data
    assert "tags" not in data
    assert "testcases" not in data


def test_submission_isolation_and_structured_feedback(client):
    admin, _ = add_user("admin", "admin")
    _, owner_token = add_user("owner")
    _, other_token = add_user("other")
    add_problem(admin.id)
    response = client.post(
        "/api/v1/submissions",
        headers=auth(owner_token),
        json={
            "problem_id": "sum-two",
            "language": "cpp20",
            "source_code": "int main(){}",
        },
    )
    assert response.status_code == 202
    submission_id = response.json()["submission_id"]
    assert submission_id == 1
    assert (
        client.get(
            f"/api/v1/submissions/{submission_id}", headers=auth(other_token)
        ).status_code
        == 404
    )
    with SessionLocal() as db:
        submission = db.get(Submission, submission_id)
        submission.status = "FINISHED"
        submission.verdict = "WA"
        submission.compile_result = json.dumps({"success": True, "stderr": ""})
        submission.judge_result = json.dumps(
            {
                "verdict": "WA",
                "summary": "Wrong answer on test 1.",
                "tests": {"total": 2, "passed": 0, "failed_test": 1},
                "failure": {
                    "test_index": 1,
                    "input": "1 2\n",
                    "expected": "3\n",
                    "actual": "4\n",
                },
                "resources": {"time_ms": 8, "memory_kb": 0},
            }
        )
        db.commit()
    feedback = client.get(
        f"/api/v1/agent/submissions/{submission_id}/feedback", headers=auth(owner_token)
    )
    assert feedback.status_code == 200
    assert feedback.json()["failure"]["expected"] == "3\n"


def test_anonymous_mutation_is_rejected(client):
    response = client.post(
        "/api/v1/submissions",
        json={
            "problem_id": "missing",
            "language": "cpp20",
            "source_code": "int main(){}",
        },
    )
    assert response.status_code == 401
