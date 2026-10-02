from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from test_phase4 import bearer, seed_problem, seed_user

from minioj import database
from minioj.config import settings
from minioj.database import SessionLocal
from minioj.models import Problem, Submission, SubmissionJudgeRun, User
from minioj.schemas import SubmissionCreate
from minioj.submissions import IdempotencyConflict, enqueue_submission

PAYLOAD = {"problem_id": "phase-four", "source_code": "int main(){}"}


def post(client, token, key="same-key", payload=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post("/api/v1/submissions", headers=headers, json=payload or PAYLOAD)


def test_retry_is_same_submission_even_after_finish_queue_fill_and_deletion(
    client, monkeypatch
):
    uid, token = seed_user("RetryOwner")
    seed_problem(uid)
    first = post(client, token)
    assert first.status_code == 202
    assert post(client, token).json() == first.json()
    with SessionLocal() as db:
        row = db.get(Submission, first.json()["submission_id"])
        row.status, row.verdict = "FINISHED", "AC"
        row.judge_result = json.dumps({"verdict": "AC"})
        db.get(Problem, "phase-four").deleted_at = row.created_at
        db.commit()
    monkeypatch.setattr(
        "minioj.server.api.settings", replace(settings, max_queued_submissions=0)
    )
    replay = post(client, token)
    assert replay.status_code == 202 and replay.json() == first.json()
    with SessionLocal() as db:
        assert db.query(Submission).count() == db.query(SubmissionJudgeRun).count() == 1
        row = db.get(Submission, 1)
        assert row.status == "FINISHED" and row.verdict == "AC"
        assert (
            row.idempotency_key_hash != "same-key"
            and len(row.idempotency_key_hash) == 64
        )


def test_keys_are_user_scoped_different_payload_conflicts_and_legacy_still_creates(
    client,
):
    uid, token = seed_user("KeyOwner")
    _, other = seed_user("KeyOther")
    seed_problem(uid)
    assert post(client, token).json()["submission_id"] == 1
    assert post(client, other).json()["submission_id"] == 2
    conflict = post(
        client, token, payload={**PAYLOAD, "source_code": "int main(){return 1;}"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert "source_code" not in conflict.text and "same-key" not in conflict.text
    assert post(client, token, key=None).json()["submission_id"] == 3
    assert post(client, token, key=None).json()["submission_id"] == 4
    assert post(client, token, key="another-key").json()["submission_id"] == 5


@pytest.mark.parametrize("key", ["", "has space", "x" * 129])
def test_invalid_keys_are_safe_422_without_creation(client, key):
    uid, token = seed_user("BadKeyOwner")
    seed_problem(uid)
    response = post(client, token, key=key)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    with SessionLocal() as db:
        assert db.query(Submission).count() == 0


@pytest.mark.parametrize("same_payload", [True, False])
def test_concurrent_connections_do_not_depend_on_api_mutex(client, same_payload):
    uid, _ = seed_user("ConcurrentKey")
    seed_problem(uid)
    barrier = threading.Barrier(6)

    def create(i):
        with SessionLocal() as db:
            user = db.get(User, uid)
            payload = SubmissionCreate(
                **{
                    **PAYLOAD,
                    "source_code": "int main(){}"
                    if same_payload
                    else f"int main(){{return {i};}}",
                }
            )
            barrier.wait(timeout=5)
            try:
                return enqueue_submission(
                    db, user, payload, idempotency_key="parallel"
                ).id
            except IdempotencyConflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, range(6)))
    assert len({r for r in results if type(r) is int}) == 1
    assert results.count("conflict") == (0 if same_payload else 5)
    with SessionLocal() as db:
        assert db.query(Submission).count() == db.query(SubmissionJudgeRun).count() == 1
        row = db.get(Submission, 1)
        db.add(
            Submission(
                user_id=uid,
                problem_id="phase-four",
                source_code="copy",
                idempotency_key_hash=row.idempotency_key_hash,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_idempotency_upgrade_preserves_old_rows_and_is_repeatable(
    tmp_path, monkeypatch
):
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy-requests.db'}")
    with legacy.begin() as connection:
        connection.exec_driver_sql("""CREATE TABLE submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            problem_id VARCHAR(80) NOT NULL, language VARCHAR(20) NOT NULL,
            source_code TEXT NOT NULL, status VARCHAR(16) NOT NULL, verdict VARCHAR(8),
            created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME,
            compile_result TEXT, judge_result TEXT)""")
        connection.exec_driver_sql(
            "INSERT INTO submissions (user_id,problem_id,language,source_code,status,verdict,created_at) VALUES (1,'old-problem','cpp20','old source','FINISHED','AC','2026-01-01')"
        )
    monkeypatch.setattr(database, "engine", legacy)
    try:
        database.init_db()
        database.init_db()
        columns = {c["name"] for c in inspect(legacy).get_columns("submissions")}
        assert {"idempotency_key_hash", "request_payload_sha256"} <= columns
        assert any(
            i["name"] == "uq_submission_user_request" and i["unique"]
            for i in inspect(legacy).get_indexes("submissions")
        )
        with legacy.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT id,source_code,verdict,idempotency_key_hash,request_payload_sha256 FROM submissions"
            ).one()
            assert tuple(row) == (1, "old source", "AC", None, None)
    finally:
        legacy.dispose()
