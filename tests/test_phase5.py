from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_phase4 import bearer, login, seed_user

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.feedback import bounded_text, safe_diagnostic, submission_feedback
from minioj.models import Problem, Submission
from minioj.problems import add_testcase
from minioj.schemas import FeedbackResponse
from minioj.server.main import app

HIDDEN = "MINIOJ_HIDDEN_SENTINEL_93AF72"
SAMPLE = "MINIOJ_PUBLIC_SAMPLE_42"
MODES = ["full", "diagnostic", "verdict_only"]
VERDICTS = ["AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "IE"]
REQUIRED = {
    "submission_id",
    "status",
    "verdict",
    "feedback_mode",
    "failed_test",
    "compile",
    "execution",
    "diagnostic",
    "summary",
    "summary_truncated",
}


def fixture_result(owner, state="FINISHED", verdict="WA"):
    with SessionLocal() as db:
        problem = Problem(
            id="phase-five", title="Safe", statement="Public", created_by=owner
        )
        db.add(problem)
        db.commit()
        add_testcase(db, problem, "sample", SAMPLE, SAMPLE)
        add_testcase(db, problem, "hidden", HIDDEN, HIDDEN)
        add_testcase(db, problem, "generated", HIDDEN + "GENERATED", HIDDEN)
        row = Submission(
            user_id=owner,
            problem_id=problem.id,
            problem_revision=problem.revision,
            source_code="int main(){}",
            status=state,
            verdict=verdict if state == "FINISHED" else None,
            compile_result=json.dumps(
                {
                    "success": verdict != "CE",
                    "time_ms": 1,
                    "exit_code": 1 if verdict == "CE" else 0,
                    "stdout": "warning: useful compiler diagnostic",
                    "stderr": "main.cpp:1: error: expected ';'\n",
                    "internal": HIDDEN,
                }
            ),
            judge_result=json.dumps(
                {
                    "verdict": verdict,
                    "summary": HIDDEN,
                    "tests": {
                        "total": 3,
                        "passed": 1,
                        "failed_test": 2,
                        "internal": HIDDEN,
                    },
                    "resources": {"time_ms": 10, "memory_kb": None, "path": HIDDEN},
                    "failure": {
                        "test_index": 2,
                        "is_sample": False,
                        "input": HIDDEN,
                        "expected": HIDDEN,
                        "actual": HIDDEN,
                        "stderr": HIDDEN,
                        "internal": HIDDEN,
                    },
                    "test_results": [
                        {
                            "test_index": 1,
                            "verdict": "AC",
                            "time_ms": 0,
                            "memory_kb": 0,
                            "input": HIDDEN,
                        },
                        {
                            "test_index": 2,
                            "verdict": verdict,
                            "time_ms": 10,
                            "memory_kb": None,
                            "stderr": HIDDEN,
                        },
                    ],
                    "testcase_types": ["sample", "hidden", "generated"],
                    "limits": {"time_ms": 1000, "memory_mb": 128, "private": HIDDEN},
                    "new_future_field": {"secret": HIDDEN},
                    "traceback": HIDDEN,
                }
            )
            if state == "FINISHED"
            else None,
        )
        db.add(row)
        db.commit()
        return row.id


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("verdict", VERDICTS)
def test_verdict_mode_ownership_allowlist_matrix(
    client, monkeypatch, mode, verdict, caplog
):
    uid, token = seed_user("FeedbackOwner")
    _, other = seed_user("FeedbackOther")
    _, admin = seed_user("FeedbackAdmin", role="admin")
    _, system = seed_user("FeedbackSystem", role="system")
    sid = fixture_result(uid, verdict=verdict)
    for module in ("api", "web", "management"):
        monkeypatch.setattr(
            f"minioj.server.{module}.settings", replace(settings, feedback_policy=mode)
        )
    feedback_path = f"/api/v1/agent/submissions/{sid}/feedback"
    for raw, status_code in (
        (token, 200),
        (other, 404),
        (admin, 200),
        (system, 200),
        (None, 401),
    ):
        response = client.get(
            feedback_path + "?feedback_mode=full", headers=bearer(raw) if raw else {}
        )
        assert response.status_code == status_code
        if status_code != 200:
            assert HIDDEN not in response.text
            continue
        body = response.json()
        FeedbackResponse.model_validate(body)
        assert REQUIRED <= body.keys()
        assert body["status"] == "FINISHED" and body["verdict"] == verdict
        assert body["feedback_mode"] == mode and body["failed_test"] == 2
        allowed_hidden = raw in {admin, system} and mode == "full" and verdict != "IE"
        assert (HIDDEN in response.text) == allowed_hidden
        assert "new_future_field" not in body and "internal" not in response.text
        if mode == "verdict_only":
            assert body["compile"] is body["execution"] is body["diagnostic"] is None
            assert "test_results" not in body and "resources" not in body
        elif verdict == "CE":
            assert "expected ';'" in body["compile"]["stderr"]
        elif verdict == "IE":
            assert body["compile"] is None and "input" not in body["failure"]
    # Search the final HTTP bodies, not merely dictionary keys.
    for path in (
        "/api/v1/problems",
        "/api/v1/problems?page=1",
        "/api/v1/problems/phase-five",
        "/api/v1/agent/problems/phase-five",
        "/problems/phase-five",
        "/problems",
        f"/api/v1/submissions/{sid}",
        "/api/v1/problems/no-such-problem",
    ):
        response = client.get(path, headers=bearer(token))
        assert HIDDEN not in response.text
        if path.endswith("/phase-five"):
            assert SAMPLE in response.text
    login(client, "FeedbackOwner")
    for path in (f"/submissions/{sid}", f"/submissions/{sid}/history/1"):
        response = client.get(path)
        assert HIDDEN not in response.text
    assert HIDDEN not in caplog.text and token not in caplog.text


@pytest.mark.parametrize("state", ["QUEUED", "COMPILING", "RUNNING"])
@pytest.mark.parametrize("mode", MODES)
def test_pending_feedback_uses_http_200_and_fixed_null_fields(
    client, monkeypatch, state, mode
):
    uid, token = seed_user("PendingOwner")
    sid = fixture_result(uid, state=state)
    monkeypatch.setattr(
        "minioj.server.api.settings", replace(settings, feedback_policy=mode)
    )
    response = client.get(
        f"/api/v1/agent/submissions/{sid}/feedback", headers=bearer(token)
    )
    assert response.status_code == 200
    body = response.json()
    assert REQUIRED <= body.keys() and body["status"] == state
    assert (
        body["verdict"]
        is body["failed_test"]
        is body["compile"]
        is body["execution"]
        is None
    )
    submission = client.get(f"/api/v1/submissions/{sid}", headers=bearer(token)).json()
    assert (
        submission["verdict"] is submission["tests"] is submission["resources"] is None
    )


@pytest.mark.parametrize(
    "value", ["x" * 1023, "x" * 1024, "x" * 1025, "中" * 342, "🙂" * 257]
)
def test_all_feedback_texts_are_utf8_bounded_and_flags_survive(value):
    clipped, flag = bounded_text(value)
    assert len(clipped.encode()) <= 1024
    assert flag == (len(value.encode()) > 1024)
    assert bounded_text(clipped, truncated=True)[1] is True
    assert safe_diagnostic(value)[1] == flag


def test_diagnostics_redact_paths_tokens_env_and_infrastructure():
    text = (
        "/work/main.cpp:5: error: expected ';'\n"
        "C:\\host\\secret.cpp:1: warning: test\n"
        "token=do-not-echo\nAuthorization: Bearer oj_private_key\n"
        "Docker failed, /home/user/.env\nTraceback (most recent call last)\n"
        "SELECT password FROM users\n" + settings.secret_key
    )
    cleaned, _ = safe_diagnostic(text)
    assert "expected ';'" in cleaned
    for private in (
        "/work",
        "C:\\",
        "oj_private",
        "do-not-echo",
        "Docker",
        "Traceback",
        ".env",
        "SELECT",
        settings.secret_key,
    ):
        assert private not in cleaned


def test_sample_content_still_available_only_in_full(client, monkeypatch):
    uid, token = seed_user("SampleOwner")
    sid = fixture_result(uid)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        stored = json.loads(row.judge_result)
        stored["tests"]["failed_test"] = 1
        stored["failure"] = {
            "test_index": 1,
            "is_sample": True,
            "input": SAMPLE,
            "expected": SAMPLE,
            "actual": "中" * 400,
            "stderr": "runtime text",
        }
        row.judge_result = json.dumps(stored)
        db.commit()
    for mode in MODES:
        monkeypatch.setattr(
            "minioj.server.api.settings", replace(settings, feedback_policy=mode)
        )
        response = client.get(
            f"/api/v1/agent/submissions/{sid}/feedback", headers=bearer(token)
        )
        assert (SAMPLE in response.text) == (mode == "full")
        if mode == "full":
            failure = response.json()["failure"]
            assert failure["actual_truncated"] is True
            assert len(failure["actual"].encode()) <= 1024


def test_malformed_legacy_results_do_not_expand_the_allowlist(client):
    uid, _ = seed_user("LegacyOwner")
    sid = fixture_result(uid)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        row.judge_result = (
            '{"resources":{"time_ms":"'
            + HIDDEN
            + '"},"test_results":[null,{"test_index":1,"verdict":[]}],"testcase_types":[{}]}'
        )
        result = submission_feedback(row, "full")
        assert HIDDEN not in json.dumps(result)
        row.judge_result = "invalid JSON " + HIDDEN
        assert HIDDEN not in json.dumps(submission_feedback(row, "full"))
        with pytest.raises(ValueError):
            submission_feedback(row, "unrecognized")


def test_unhandled_api_errors_are_safe_and_use_original_envelope(client, caplog):
    from minioj.database import get_db

    def broken():
        raise RuntimeError(HIDDEN + " SQL /home/user/oj.db oj_private_secret")
        yield  # pragma: no cover

    app.dependency_overrides[get_db] = broken
    try:
        with TestClient(app, raise_server_exceptions=False) as isolated:
            response = isolated.get("/api/v1/problems")
        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "details": None,
            },
            "detail": "Internal server error",
        }
        assert HIDDEN not in caplog.text and "oj_private_secret" not in caplog.text
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_openapi_freezes_feedback_submission_queries_and_errors(client):
    schema = client.get("/openapi.json").json()
    models = schema["components"]["schemas"]
    assert REQUIRED <= set(models["FeedbackResponse"]["required"])
    for name in ("FeedbackResponse", "SubmissionDetailResponse"):
        assert models[name]["properties"]["status"]["enum"] == [
            "QUEUED",
            "COMPILING",
            "RUNNING",
            "FINISHED",
        ]
        assert models[name]["properties"]["verdict"]["anyOf"][0]["enum"] == VERDICTS
    assert models["FeedbackResponse"]["properties"]["feedback_mode"]["enum"] == MODES
    assert set(models["SubmissionDetailResponse"]["required"]) == {
        "submission_id",
        "problem_id",
        "language",
        "status",
        "verdict",
        "tests",
        "resources",
        "created_at",
        "started_at",
        "finished_at",
    }
    feedback = schema["paths"]["/api/v1/agent/submissions/{submission_id}/feedback"][
        "get"
    ]
    assert feedback["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/FeedbackResponse")
    assert feedback["responses"]["404"]["content"]["application/json"]["schema"][
        "$ref"
    ].endswith("/ErrorResponse")
    queries = {
        p["name"]: p for p in schema["paths"]["/api/v1/problems"]["get"]["parameters"]
    }
    assert set(queries) == {"page", "sort", "q"}
    assert queries["sort"]["schema"]["enum"] == [
        "default",
        "difficulty_asc",
        "difficulty_desc",
    ]
    assert queries["page"]["schema"]["anyOf"][0]["minimum"] == 1
    response_headers = schema["paths"]["/api/v1/problems"]["get"]["responses"]["200"][
        "headers"
    ]
    assert set(response_headers) == {
        "X-Total-Count",
        "X-Page",
        "X-Page-Size",
        "X-Total-Pages",
    }
    assert all(
        header["schema"]["type"] == "integer" for header in response_headers.values()
    )
    assert response_headers["X-Page-Size"]["schema"]["minimum"] == 50
    headers = schema["paths"]["/api/v1/submissions"]["post"]["parameters"]
    assert any(
        p["name"] == "idempotency-key" and p["in"] == "header" and not p["required"]
        for p in headers
    )
