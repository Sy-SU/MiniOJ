from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.models import ApiToken, CustomRun, Problem, Submission, User
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
    admin, admin_token = add_user("admin", "admin")
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
    assert feedback.json()["failure"] == {"test_index": 1}

    admin_feedback = client.get(
        f"/api/v1/agent/submissions/{submission_id}/feedback", headers=auth(admin_token)
    )
    assert admin_feedback.json()["failure"]["expected"] == "3\n"


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


def _process_one_custom_run() -> None:
    from minioj.worker.main import claim_next_custom_run, process_custom_run

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        job_id = claim_next_custom_run()
        if job_id is not None:
            process_custom_run(job_id)
            return
        time.sleep(0.01)
    raise AssertionError("Custom Run was not queued")


def test_custom_run_accepts_source_code_and_legacy_code(client, monkeypatch):
    _, token = add_user("runner")
    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.custom_run",
        lambda _judge, source, stdin: {
            "status": "OK",
            "exit_code": 0,
            "stdout": source + stdin,
            "stderr": "",
            "time_ms": 1,
            "memory_kb": None,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "output_truncated": False,
        },
    )

    for field in ("source_code", "code"):
        worker = threading.Thread(target=_process_one_custom_run)
        worker.start()
        response = client.post(
            "/api/v1/runs",
            headers=auth(token),
            json={field: "source", "stdin": "input"},
        )
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert response.status_code == 200
        assert response.json()["stdout"] == "sourceinput"

    with SessionLocal() as db:
        assert db.query(CustomRun).count() == 0


def test_custom_run_rejects_conflicting_source_fields(client):
    _, token = add_user("runner")
    response = client.post(
        "/api/v1/runs",
        headers=auth(token),
        json={"source_code": "one", "code": "two"},
    )
    assert response.status_code == 422


def test_queue_capacity_returns_retry_after(client, monkeypatch):
    admin, _ = add_user("admin", "admin")
    user, token = add_user("queued")
    add_problem(admin.id)
    api_settings = replace(
        settings,
        max_queued_submissions=1,
        max_queued_runs=1,
        overload_retry_after_seconds=7,
    )
    monkeypatch.setattr("minioj.server.api.settings", api_settings)
    with SessionLocal() as db:
        db.add(
            Submission(
                user_id=user.id,
                problem_id="sum-two",
                source_code="queued",
                status="QUEUED",
            )
        )
        db.add(CustomRun(user_id=user.id, source_code="queued", status="QUEUED"))
        db.commit()

    submission = client.post(
        "/api/v1/submissions",
        headers=auth(token),
        json={"problem_id": "sum-two", "source_code": "new"},
    )
    custom_run = client.post(
        "/api/v1/runs", headers=auth(token), json={"source_code": "new"}
    )
    for response in (submission, custom_run):
        assert response.status_code == 429
        assert response.headers["retry-after"] == "7"


def test_custom_run_wait_timeout_cancels_unclaimed_job(client, monkeypatch):
    _, token = add_user("runner")
    monkeypatch.setattr(
        "minioj.server.api.settings",
        replace(settings, custom_run_wait_seconds=0.02),
    )
    response = client.post(
        "/api/v1/runs", headers=auth(token), json={"source_code": "source"}
    )
    assert response.status_code == 503
    assert "timed out" in response.json()["detail"]["summary"]
    with SessionLocal() as db:
        job = db.query(CustomRun).one()
        assert job.status == "CANCELLED"


def test_feedback_policy_is_shared_by_agent_and_web(client, monkeypatch):
    admin, _ = add_user("admin", "admin")
    _, token = add_user("feedback")
    add_problem(admin.id)
    with SessionLocal() as db:
        user = db.query(User).filter_by(username="feedback").one()
        submission = Submission(
            user_id=user.id,
            problem_id="sum-two",
            source_code="source",
            status="FINISHED",
            verdict="WA",
            compile_result=json.dumps({"success": True}),
            judge_result=json.dumps(
                {
                    "verdict": "WA",
                    "summary": "Wrong answer.",
                    "tests": {"total": 1, "passed": 0, "failed_test": 1},
                    "failure": {
                        "test_index": 1,
                        "input": "secret input",
                        "expected": "secret expected",
                        "actual": "actual",
                    },
                    "resources": {"time_ms": 1, "memory_kb": None},
                }
            ),
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    policy_settings = replace(settings, feedback_policy="diagnostic")
    monkeypatch.setattr("minioj.server.api.settings", policy_settings)
    monkeypatch.setattr("minioj.server.web.settings", policy_settings)
    feedback = client.get(
        f"/api/v1/agent/submissions/{submission_id}/feedback", headers=auth(token)
    )
    assert feedback.json()["failure"] == {"test_index": 1}

    # Establish the same user session for the browser route.
    login = client.get("/login")
    csrf = login.cookies.get("minioj_session")
    assert csrf is not None
    match = re.search(r'name="csrf_token" value="([^"]+)"', login.text)
    assert match
    response = client.post(
        "/login",
        data={
            "csrf_token": match.group(1),
            "identity": "feedback",
            "password": "correct-horse-battery",
        },
    )
    assert response.status_code == 200
    page = client.get(f"/submissions/{submission_id}")
    assert "First failed test:" not in page.text
    assert "Wrong answer." in page.text
    assert "secret input" not in page.text
    assert "secret expected" not in page.text
