from __future__ import annotations

import hashlib
import re
from dataclasses import replace

import pytest

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.judge import TestcaseBuildError as BuildError
from minioj.models import Problem, Submission, User
from minioj.models import TestCase as CaseModel
from minioj.models import TestcaseBuild as BuildModel
from minioj.problems import testcase_contents as read_testcase_contents
from minioj.security import hash_password
from minioj.worker.main import claim_next_testcase_build, process_testcase_build

PASSWORD = "generator-test-password"
STANDARD = b"// STD-PRIVATE-MARKER\nint main() { return 0; }\n"


def csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def create_admin_and_problem(client, problem_id: str = "generator-case") -> int:
    with SessionLocal() as db:
        admin = User(
            username="GeneratorAdmin",
            email="generator-admin@example.com",
            password_hash=hash_password(PASSWORD),
            role="admin",
        )
        db.add(admin)
        db.flush()
        problem = Problem(
            id=problem_id,
            title="Generator case",
            statement="Build tests.",
            created_by=admin.id,
        )
        db.add(problem)
        db.commit()
        admin_id = admin.id
    token = csrf(client.get("/login").text)
    response = client.post(
        "/login",
        data={"csrf_token": token, "identity": "GeneratorAdmin", "password": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return admin_id


def upload_standard(client, problem_id: str = "generator-case") -> str:
    editor = client.get(f"/admin/problems/{problem_id}/edit")
    assert f"/manage/problems/{problem_id}/standard-solution" in editor.text
    token = csrf(editor.text)
    response = client.post(
        f"/admin/problems/{problem_id}/standard-solution",
        data={"csrf_token": token},
        files={"standard_file": ("std.cpp", STANDARD, "text/x-c++src")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    editor = client.get(f"/admin/problems/{problem_id}/edit")
    assert f"/manage/problems/{problem_id}/testcase-builds/input" in editor.text
    assert f"/manage/problems/{problem_id}/testcase-builds/generator" in editor.text
    return token


def test_input_upload_queues_worker_and_std_computes_expected_output(
    client, monkeypatch
):
    create_admin_and_problem(client)
    token = upload_standard(client)
    input_data = b"2 3\n"
    queued = client.post(
        "/admin/problems/generator-case/testcase-builds/input",
        data={"csrf_token": token, "type": "sample"},
        files={"input_file": ("case.in", input_data, "text/plain")},
        follow_redirects=False,
    )
    assert queued.status_code == 303

    with SessionLocal() as db:
        problem = db.get(Problem, "generator-case")
        build = db.query(BuildModel).one()
        assert problem.standard_sha256 == hashlib.sha256(STANDARD).hexdigest()
        assert build.status == "QUEUED"
        assert build.input_data == "2 3\n"
        assert build.standard_source == STANDARD.decode()

    def build_testcases(_judge, standard_source, **kwargs):
        assert "STD-PRIVATE-MARKER" in standard_source
        assert kwargs["input_data"] == "2 3\n"
        assert kwargs["generator_source"] is None
        return [(kwargs["input_data"], "5\n")]

    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases", build_testcases
    )
    build_id = claim_next_testcase_build()
    assert build_id is not None
    process_testcase_build(build_id)

    with SessionLocal() as db:
        build = db.get(BuildModel, build_id)
        testcase = db.query(CaseModel).one()
        assert build.status == "FINISHED"
        assert build.created_count == 1
        assert testcase.type == "sample"
        assert read_testcase_contents(testcase) == ("2 3\n", "5\n")

    public = client.get("/api/v1/problems/generator-case")
    assert public.status_code == 200
    assert "STD-PRIVATE-MARKER" not in public.text
    assert "2 3" in client.get("/problems/generator-case").text


def test_generator_job_builds_atomic_generated_batch(client, monkeypatch):
    create_admin_and_problem(client)
    token = upload_standard(client)
    generator = b"// GENERATOR-PRIVATE-MARKER\nint main() { return 0; }\n"
    queued = client.post(
        "/admin/problems/generator-case/testcase-builds/generator",
        data={"csrf_token": token, "case_count": "2", "base_seed": "41"},
        files={"generator_file": ("gen.cpp", generator, "text/x-c++src")},
        follow_redirects=False,
    )
    assert queued.status_code == 303

    def build_testcases(_judge, _standard_source, **kwargs):
        assert kwargs["generator_source"] == generator.decode()
        assert kwargs["case_count"] == 2
        assert kwargs["base_seed"] == 41
        return [("41 1\n", "42\n"), ("42 2\n", "44\n")]

    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases", build_testcases
    )
    build_id = claim_next_testcase_build()
    assert build_id is not None
    process_testcase_build(build_id)

    with SessionLocal() as db:
        build = db.get(BuildModel, build_id)
        testcases = db.query(CaseModel).order_by(CaseModel.order).all()
        assert build.status == "FINISHED"
        assert build.created_count == 2
        assert [row.type for row in testcases] == ["generated", "generated"]
        assert [read_testcase_contents(row) for row in testcases] == [
            ("41 1\n", "42\n"),
            ("42 2\n", "44\n"),
        ]
    page = client.get("/problems/generator-case")
    assert "GENERATOR-PRIVATE-MARKER" not in page.text
    assert "41 1" not in page.text


def test_build_can_finish_after_first_submission(client, monkeypatch):
    admin_id = create_admin_and_problem(client)
    token = upload_standard(client)
    client.post(
        "/admin/problems/generator-case/testcase-builds/input",
        data={"csrf_token": token, "type": "hidden"},
        files={"input_file": ("case.in", b"1\n", "text/plain")},
    )
    with SessionLocal() as db:
        user = User(
            username="Submitter",
            email="submitter-generator@example.com",
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        db.flush()
        db.add(
            Submission(
                user_id=user.id,
                problem_id="generator-case",
                source_code="int main(){}",
            )
        )
        db.commit()

    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases",
        lambda *_args, **_kwargs: [("1\n", "1\n")],
    )
    build_id = claim_next_testcase_build()
    assert build_id is not None
    process_testcase_build(build_id)
    with SessionLocal() as db:
        assert db.get(BuildModel, build_id).status == "FINISHED"
        assert db.query(CaseModel).count() == 1
        submission = db.query(Submission).one()
        assert submission.problem_revision != submission.problem.revision
        assert db.get(User, admin_id) is not None


def test_compiler_or_generator_failure_is_recorded_for_admin(client, monkeypatch):
    create_admin_and_problem(client)
    token = upload_standard(client)
    client.post(
        "/admin/problems/generator-case/testcase-builds/generator",
        data={"csrf_token": token, "case_count": "1", "base_seed": "1"},
        files={"generator_file": ("bad.cpp", b"broken", "text/plain")},
    )
    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            BuildError("Generator compilation exited with code 1: syntax error")
        ),
    )
    build_id = claim_next_testcase_build()
    assert build_id is not None
    process_testcase_build(build_id)
    with SessionLocal() as db:
        build = db.get(BuildModel, build_id)
        assert build.status == "FAILED"
        assert "syntax error" in build.error
        assert db.query(CaseModel).count() == 0
    admin_page = client.get("/admin/problems/generator-case/edit")
    assert "syntax error" in admin_page.text


def test_regular_user_cannot_upload_standard_or_queue_generator(client):
    with SessionLocal() as db:
        user = User(
            username="GeneratorUser",
            email="generator-user@example.com",
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        db.commit()
    token = csrf(client.get("/login").text)
    client.post(
        "/login",
        data={"csrf_token": token, "identity": "GeneratorUser", "password": PASSWORD},
    )
    session_token = csrf(client.get("/settings").text)

    standard = client.post(
        "/admin/problems/missing/standard-solution",
        data={"csrf_token": session_token},
        files={"standard_file": ("std.cpp", STANDARD, "text/x-c++src")},
    )
    generator = client.post(
        "/admin/problems/missing/testcase-builds/generator",
        data={"csrf_token": session_token, "case_count": "1", "base_seed": "1"},
        files={"generator_file": ("gen.cpp", b"int main(){}", "text/x-c++src")},
    )
    assert standard.status_code == 403
    assert generator.status_code == 403


def test_standard_source_can_be_pasted_edited_and_safely_displayed(client):
    create_admin_and_problem(client)
    token = upload_standard(client)
    path = "/admin/problems/generator-case/edit"
    assert STANDARD.decode().strip() in client.get(path).text
    source = '// 私有源码\n// </textarea><script id="std-xss">alert(1)</script>\nint main() { return 2; }\n'
    response = client.post(
        "/admin/problems/generator-case/standard-solution",
        data={"csrf_token": token},
        files={"standard_source": (None, source)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(path)
    assert "&lt;/textarea&gt;&lt;script" in page.text
    assert '<script id="std-xss">' not in page.text
    assert 'id="standard-source"' in page.text
    assert "return 2;" in page.text
    assert "/static/alerts.js" in page.text
    with SessionLocal() as db:
        problem = db.get(Problem, "generator-case")
        assert problem.standard_source == source
        assert problem.standard_sha256 == hashlib.sha256(source.encode()).hexdigest()
    for path in ("/problems/generator-case", "/api/v1/problems/generator-case"):
        assert "std-xss" not in client.get(path).text


@pytest.mark.parametrize("invalid", ["blank", "too_large", "both"])
def test_invalid_pasted_source_preserves_saved_std_and_draft(
    client, monkeypatch, invalid
):
    create_admin_and_problem(client)
    token = upload_standard(client)
    limited_settings = replace(settings, source_limit_bytes=64)
    monkeypatch.setattr("minioj.server.web.settings", limited_settings)
    monkeypatch.setattr("minioj.testcase_builds.settings", limited_settings)
    source = (
        " "
        if invalid == "blank"
        else ("中" * 22 if invalid == "too_large" else "int main(){}")
    )
    files = {"standard_source": (None, source)}
    if invalid == "both":
        files["standard_file"] = ("std.cpp", b"int main(){}")
    response = client.post(
        "/admin/problems/generator-case/standard-solution",
        data={"csrf_token": token},
        files=files,
    )
    assert response.status_code == 422
    assert 'class="alert alert-error"' in response.text
    assert source in response.text
    with SessionLocal() as db:
        assert db.get(Problem, "generator-case").standard_source == STANDARD.decode()


def test_pasted_std_requires_admin_and_csrf(client):
    admin_id = create_admin_and_problem(client)
    path = "/admin/problems/generator-case/standard-solution"
    assert (
        client.post(path, data={"standard_source": "int main(){}"}).status_code == 403
    )
    token = csrf(client.get("/admin/problems/generator-case/edit").text)
    with SessionLocal() as db:
        db.get(User, admin_id).role = "user"
        db.commit()
    assert (
        client.post(
            path, data={"csrf_token": token, "standard_source": "int main(){}"}
        ).status_code
        == 403
    )
    assert client.get("/admin/problems/generator-case/edit").status_code == 403
    with SessionLocal() as db:
        assert db.get(Problem, "generator-case").standard_source is None
