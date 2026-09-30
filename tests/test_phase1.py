from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.models import ApiToken, Problem, Sample, Submission, User
from minioj.models import TestCase as CaseModel
from minioj.problems import (
    add_testcase,
    delete_problem,
    problem_test_dir,
    update_testcase,
)
from minioj.problems import testcase_contents as read_testcase_contents
from minioj.security import create_api_token, hash_password

PASSWORD = "phase-one-test-password"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def create_user(
    name: str, *, role: str = "user", active: bool = True
) -> tuple[int, str]:
    token_id, raw, digest, expires = create_api_token()
    with SessionLocal() as db:
        user = User(
            username=name,
            email=f"{name}@example.com",
            password_hash=hash_password(PASSWORD),
            role=role,
            is_active=active,
        )
        db.add(user)
        db.flush()
        db.add(
            ApiToken(
                id=token_id,
                user_id=user.id,
                name="phase-one",
                token_hash=digest,
                expires_at=expires,
            )
        )
        db.commit()
        return user.id, raw


def login(client, name: str) -> None:
    token = csrf(client.get("/login").text)
    response = client.post(
        "/login",
        data={"csrf_token": token, "identity": name, "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303


def create_problem(owner_id: int, problem_id: str = "phase-one") -> Problem:
    with SessionLocal() as db:
        problem = Problem(
            id=problem_id,
            title="Phase One",
            statement="Statement",
            created_by=owner_id,
        )
        db.add(problem)
        db.commit()
        db.refresh(problem)
        db.expunge(problem)
        return problem


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def problem_payload(problem_id: str, title: str = "Updated") -> dict:
    return {
        "id": problem_id,
        "title": title,
        "statement": "Updated statement",
        "input_specification": "Input",
        "output_specification": "Output",
        "notes": "Notes",
        "time_limit_ms": 1000,
        "memory_limit_mb": 128,
        "tags": "test",
    }


def test_phase_one_browser_acceptance_flow(client):
    create_user("FlowAdmin", role="admin")
    login(client, "FlowAdmin")
    token = csrf(client.get("/admin/problems/new").text)
    created = client.post(
        "/admin/problems/new",
        data={
            "csrf_token": token,
            "id": "browser-flow",
            "title": "Browser Flow",
            "statement": "Add the values.",
            "input_specification": "Two integers.",
            "output_specification": "Their sum.",
            "notes": "Public note.",
            "time_limit_ms": "1000",
            "memory_limit_mb": "128",
            "rating": "800",
            "source": "manual",
            "source_id": "flow-1",
            "source_url": "",
            "tags": "math",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/admin/problems/browser-flow/edit"

    edit_path = "/admin/problems/browser-flow/edit"
    token = csrf(client.get(edit_path).text)
    for testcase_type, input_data, output_data in [
        ("sample", b"2 3\n", b"5\n"),
        ("hidden", b"HIDDEN-FLOW-MARKER\n", b"42\n"),
    ]:
        response = client.post(
            "/admin/problems/browser-flow/testcases",
            data={"csrf_token": token, "type": testcase_type},
            files={
                "input_file": ("case.in", input_data, "text/plain"),
                "output_file": ("case.out", output_data, "text/plain"),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303

    client.post("/logout", data={"csrf_token": token})
    registration_token = csrf(client.get("/register").text)
    registered = client.post(
        "/register",
        data={
            "csrf_token": registration_token,
            "username": "WebUser",
            "email": "web-user@example.com",
            "password": PASSWORD,
            "password_confirmation": PASSWORD,
        },
        follow_redirects=False,
    )
    assert registered.status_code == 303
    page = client.get("/problems/browser-flow")
    assert "2 3" in page.text
    assert "HIDDEN-FLOW-MARKER" not in page.text
    assert client.get("/admin").status_code == 403
    with SessionLocal() as db:
        assert [row.order for row in db.query(CaseModel).order_by(CaseModel.order)] == [
            1,
            2,
        ]
        assert db.query(Sample).one().input == "2 3\n"


def test_admin_uploads_updates_and_deletes_testcase_with_checksums(client):
    admin_id, _ = create_user("PhaseAdmin", role="admin")
    create_problem(admin_id)
    login(client, "PhaseAdmin")
    edit_path = "/admin/problems/phase-one/edit"
    token = csrf(client.get(edit_path).text)
    input_data = b"2 3\n"
    output_data = b"5\n"
    response = client.post(
        "/admin/problems/phase-one/testcases",
        data={
            "csrf_token": token,
            "type": "sample",
            "input_sha256": hashlib.sha256(input_data).hexdigest(),
            "output_sha256": hashlib.sha256(output_data).hexdigest(),
        },
        files={
            "input_file": ("sum.in", input_data, "text/plain"),
            "output_file": ("sum.out", output_data, "text/plain"),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        testcase = db.query(CaseModel).one()
        testcase_id = testcase.id
        input_path = settings.data_dir / testcase.input_path
        old_output_path = settings.data_dir / testcase.output_path
        assert testcase.order == 1
        assert testcase.created_at is not None
        assert testcase.input_sha256 == hashlib.sha256(input_data).hexdigest()
        assert testcase.output_sha256 == hashlib.sha256(output_data).hexdigest()
        assert input_path.read_bytes() == input_data
        assert old_output_path.read_bytes() == output_data
        sample = db.query(Sample).one()
        assert sample.testcase_id == testcase.id
        assert (sample.input, sample.output) == ("2 3\n", "5\n")

    replacement = b"6\n"
    response = client.post(
        f"/admin/problems/phase-one/testcases/{testcase_id}/edit",
        data={
            "csrf_token": token,
            "type": "sample",
            "output_sha256": hashlib.sha256(replacement).hexdigest(),
        },
        files={"output_file": ("sum.out", replacement, "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        testcase = db.get(CaseModel, testcase_id)
        assert read_testcase_contents(testcase) == ("2 3\n", "6\n")
        assert db.query(Sample).one().output == "6\n"
        assert input_path.exists()
        assert not old_output_path.exists()
        current_paths = [
            settings.data_dir / testcase.input_path,
            settings.data_dir / testcase.output_path,
        ]

    response = client.post(
        f"/admin/problems/phase-one/testcases/{testcase_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        assert db.query(CaseModel).count() == 0
        assert db.query(Sample).count() == 0
    assert not any(path.exists() for path in current_paths)


def test_upload_limit_checksum_and_utf8_fail_without_metadata_or_files(client):
    admin_id, _ = create_user("UploadAdmin", role="admin")
    create_problem(admin_id, "upload-check")
    login(client, "UploadAdmin")
    path = "/admin/problems/upload-check/edit"
    token = csrf(client.get(path).text)
    original_limit = settings.testcase_file_limit_bytes
    object.__setattr__(settings, "testcase_file_limit_bytes", 32)
    try:
        too_large = client.post(
            "/admin/problems/upload-check/testcases",
            data={"csrf_token": token, "type": "hidden"},
            files={
                "input_file": ("large.in", b"x" * 33, "text/plain"),
                "output_file": ("large.out", b"ok\n", "text/plain"),
            },
        )
        assert too_large.status_code == 413

        mismatch = client.post(
            "/admin/problems/upload-check/testcases",
            data={
                "csrf_token": token,
                "type": "hidden",
                "input_sha256": "0" * 64,
            },
            files={
                "input_file": ("case.in", b"input\n", "text/plain"),
                "output_file": ("case.out", b"output\n", "text/plain"),
            },
        )
        assert "does not match" in mismatch.text

        invalid_utf8 = client.post(
            "/admin/problems/upload-check/testcases",
            data={"csrf_token": token, "type": "hidden"},
            files={
                "input_file": ("case.in", b"\xff", "application/octet-stream"),
                "output_file": ("case.out", b"output\n", "text/plain"),
            },
        )
        assert "valid UTF-8" in invalid_utf8.text
    finally:
        object.__setattr__(settings, "testcase_file_limit_bytes", original_limit)

    with SessionLocal() as db:
        assert db.query(CaseModel).count() == 0
    directory = problem_test_dir("upload-check")
    assert not directory.exists() or not list(directory.iterdir())


def test_database_failure_rolls_back_testcase_files_and_metadata(monkeypatch):
    admin_id, _ = create_user("RollbackAdmin", role="admin")
    problem = create_problem(admin_id, "rollback-case")
    with SessionLocal() as db:
        attached = db.get(Problem, problem.id)

        def fail_commit():
            raise RuntimeError("injected commit failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected"):
            add_testcase(db, attached, "hidden", "input\n", "output\n")

    with SessionLocal() as db:
        assert db.query(CaseModel).count() == 0
        assert db.query(Sample).count() == 0
    directory = problem_test_dir(problem.id)
    assert directory.exists()
    assert not list(directory.iterdir())


def test_update_failure_preserves_old_testcase_files_and_metadata(monkeypatch):
    admin_id, _ = create_user("UpdateRollback", role="admin")
    problem = create_problem(admin_id, "update-rollback")
    with SessionLocal() as db:
        testcase = add_testcase(
            db, db.get(Problem, problem.id), "hidden", "old input\n", "old output\n"
        )
        testcase_id = testcase.id
        old_paths = (testcase.input_path, testcase.output_path)

    with SessionLocal() as db:
        attached_problem = db.get(Problem, problem.id)

        def fail_commit():
            raise RuntimeError("injected update failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected update"):
            update_testcase(
                db,
                attached_problem,
                testcase_id,
                "sample",
                input_data="new input\n",
                output_data="new output\n",
            )

    with SessionLocal() as db:
        testcase = db.get(CaseModel, testcase_id)
        assert (testcase.input_path, testcase.output_path) == old_paths
        assert read_testcase_contents(testcase) == ("old input\n", "old output\n")
        assert db.query(Sample).count() == 0
    files = sorted(path.name for path in problem_test_dir(problem.id).iterdir())
    assert files == sorted(Path(path).name for path in old_paths)


def test_problem_soft_delete_rolls_back_on_commit_failure(client, monkeypatch):
    admin_id, token = create_user("DeleteRollback", role="admin")
    problem = create_problem(admin_id, "delete-rollback")
    with SessionLocal() as db:
        add_testcase(db, db.get(Problem, problem.id), "hidden", "input\n", "output\n")
    directory = problem_test_dir(problem.id).parent

    with SessionLocal() as db:
        attached = db.get(Problem, problem.id)

        def fail_commit():
            raise RuntimeError("injected delete failure")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="injected delete"):
            delete_problem(db, attached)

    assert directory.exists()
    with SessionLocal() as db:
        assert db.get(Problem, problem.id) is not None
        assert db.get(Problem, problem.id).deleted_at is None
        assert db.query(CaseModel).count() == 1

    response = client.delete(
        "/api/v1/admin/problems/delete-rollback", headers=bearer(token)
    )
    assert response.status_code == 204
    assert directory.exists()
    with SessionLocal() as db:
        assert db.get(Problem, problem.id).deleted_at is not None
        assert db.query(CaseModel).count() == 1


def test_problem_and_testcases_remain_editable_after_submission(client):
    admin_id, admin_token = create_user("HistoryAdmin", role="admin")
    user_id, _ = create_user("HistoryUser")
    problem = create_problem(admin_id, "history-case")
    with SessionLocal() as db:
        testcase = add_testcase(db, db.get(Problem, problem.id), "hidden", "1\n", "1\n")
        testcase_id = testcase.id
        submission = Submission(
            user_id=user_id,
            problem_id=problem.id,
            source_code="int main(){}",
            status="FINISHED",
            verdict="AC",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    headers = bearer(admin_token)
    assert (
        client.put(
            "/api/v1/admin/problems/history-case",
            headers=headers,
            json=problem_payload("history-case"),
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/admin/problems/history-case/testcases",
            headers=headers,
            json={"type": "hidden", "input": "2\n", "output": "2\n"},
        ).status_code
        == 201
    )
    assert (
        client.put(
            f"/api/v1/admin/problems/history-case/testcases/{testcase_id}",
            headers=headers,
            json={"type": "hidden", "input": "2\n", "output": "2\n"},
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/admin/problems/history-case/testcases/{testcase_id}",
            headers=headers,
        ).status_code
        == 204
    )
    assert (
        client.delete(
            "/api/v1/admin/problems/history-case", headers=headers
        ).status_code
        == 204
    )

    login(client, "HistoryAdmin")
    assert client.get("/admin/problems/history-case/edit").status_code == 404
    deleted_page = client.get("/problems/history-case")
    assert deleted_page.status_code == 410
    assert "This problem has been deleted" in deleted_page.text
    detail = client.get(f"/submissions/{submission_id}")
    assert detail.status_code == 200
    assert "This problem has been deleted" in detail.text
    with SessionLocal() as db:
        assert db.get(Problem, problem.id).title == "Updated"
        assert db.get(Problem, problem.id).deleted_at is not None
        assert db.get(CaseModel, testcase_id) is None
        assert db.get(Submission, submission_id).verdict == "AC"


def test_regular_user_cannot_forge_admin_requests(client):
    user_id, token = create_user("NoAdmin")
    admin_id, _ = create_user("RealAdmin", role="admin")
    create_problem(admin_id, "admin-only")
    assert (
        client.post(
            "/api/v1/admin/problems",
            headers=bearer(token),
            json=problem_payload("forged-problem"),
        ).status_code
        == 403
    )
    login(client, "NoAdmin")
    form_token = csrf(client.get("/settings").text)
    assert (
        client.post(
            "/admin/problems/admin-only/delete",
            data={"csrf_token": form_token},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/admin/users/{user_id}",
            data={"csrf_token": form_token, "role": "admin", "is_active": "on"},
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        assert db.get(User, user_id).role == "user"
        assert db.get(Problem, "admin-only") is not None


def test_disabled_user_loses_session_and_bearer_access(client):
    user_id, token = create_user("DisabledLater")
    login(client, "DisabledLater")
    assert client.get("/api/v1/me").status_code == 200
    with SessionLocal() as db:
        db.get(User, user_id).is_active = False
        db.commit()
    assert client.get("/api/v1/me").status_code == 401
    assert (
        client.get("/settings", follow_redirects=False).headers["location"] == "/login"
    )
    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_web_submission_owner_is_enforced(client):
    admin_id, _ = create_user("OwnerAdmin", role="admin")
    owner_id, _ = create_user("SubmitOwner")
    create_user("SubmitOther")
    create_problem(admin_id, "owner-case")
    with SessionLocal() as db:
        submission = Submission(
            user_id=owner_id,
            problem_id="owner-case",
            source_code="private source",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id
    login(client, "SubmitOther")
    assert client.get(f"/submissions/{submission_id}").status_code == 404


def test_testcase_paths_reject_traversal_cross_problem_and_symlink(tmp_path):
    admin_id, _ = create_user("PathAdmin", role="admin")
    first = create_problem(admin_id, "path-first")
    second = create_problem(admin_id, "path-second")
    with SessionLocal() as db:
        first_case = add_testcase(
            db, db.get(Problem, first.id), "hidden", "first\n", "one\n"
        )
        second_case = add_testcase(
            db, db.get(Problem, second.id), "hidden", "second\n", "two\n"
        )
        first_case.input_path = second_case.input_path
        with pytest.raises(ValueError, match="Unsafe testcase path"):
            read_testcase_contents(first_case)

        outside = tmp_path / "outside.in"
        outside.write_text("secret", encoding="utf-8")
        link = problem_test_dir(first.id) / "linked.in"
        link.symlink_to(outside)
        first_case.input_path = str(link.relative_to(settings.data_dir))
        with pytest.raises(ValueError, match="symlink"):
            read_testcase_contents(first_case)

    for value in ["../escape", "..", "nested/problem"]:
        with pytest.raises(ValueError, match="Unsafe problem id"):
            problem_test_dir(value)


def test_hidden_data_is_not_public_and_html_fields_are_escaped(client):
    admin_id, _ = create_user("EscapeAdmin", role="admin")
    owner_id, owner_token = create_user("EscapeOwner")
    with SessionLocal() as db:
        problem = Problem(
            id="escape-case",
            title="<script id=title>x</script>",
            statement="<script id=statement>x</script>",
            notes="<img src=x onerror=alert(1)>",
            created_by=admin_id,
        )
        db.add(problem)
        db.commit()
        add_testcase(
            db,
            problem,
            "sample",
            "<script id=sample-input>x</script>\n",
            "sample-output\n",
        )
        add_testcase(db, problem, "hidden", "HIDDEN-UNIQUE-MARKER\n", "hidden\n")
        add_testcase(
            db, problem, "generated", "GENERATED-UNIQUE-MARKER\n", "generated\n"
        )
        assert [row.order for row in problem.testcases] == [1, 2, 3]
        submission = Submission(
            user_id=owner_id,
            problem_id=problem.id,
            source_code="</code><script id=source>x</script>",
            status="FINISHED",
            verdict="WA",
            compile_result=json.dumps(
                {"success": False, "stderr": "<script id=compiler>x</script>"}
            ),
            judge_result=json.dumps(
                {
                    "verdict": "WA",
                    "summary": "<script id=summary>x</script>",
                    "tests": {"total": 3, "passed": 0},
                    "failure": {
                        "input": "<script id=failure-input>x</script>",
                        "expected": "<script id=expected>x</script>",
                        "actual": "<script id=actual>x</script>",
                    },
                    "resources": {"time_ms": 1, "memory_kb": 1},
                }
            ),
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id

    page = client.get("/problems/escape-case")
    assert page.status_code == 200
    assert "HIDDEN-UNIQUE-MARKER" not in page.text
    assert "GENERATED-UNIQUE-MARKER" not in page.text
    assert "<script id=statement>" not in page.text
    assert "&lt;script id=statement&gt;" in page.text
    assert "<script id=sample-input>" not in page.text

    public = client.get("/api/v1/problems/escape-case")
    assert public.headers["content-type"].startswith("application/json")
    assert "HIDDEN-UNIQUE-MARKER" not in public.text
    assert "GENERATED-UNIQUE-MARKER" not in public.text
    agent = client.get(
        "/api/v1/agent/problems/escape-case", headers=bearer(owner_token)
    )
    assert "HIDDEN-UNIQUE-MARKER" not in agent.text
    assert "GENERATED-UNIQUE-MARKER" not in agent.text

    assert client.get("/static/../data/problems/escape-case/tests").status_code == 404
    assert (
        client.get("/static/%2e%2e/data/problems/escape-case/tests").status_code == 404
    )

    login(client, "EscapeOwner")
    submission = client.get(f"/submissions/{submission_id}")
    for marker in [
        "source",
        "compiler",
        "summary",
        "failure-input",
        "expected",
        "actual",
    ]:
        assert f"<script id={marker}>" not in submission.text
        assert f"&lt;script id={marker}&gt;" in submission.text
