from __future__ import annotations

import json

import pytest
from test_phase1 import (
    bearer,
    create_problem,
    create_user,
    csrf,
    login,
    problem_payload,
)

from minioj.database import SessionLocal
from minioj.models import Problem, Submission, User
from minioj.models import TestCase as CaseModel
from minioj.models import TestcaseBuild as BuildModel
from minioj.problems import (
    add_testcase,
    delete_problem,
    update_problem,
    update_testcase,
)
from minioj.testcase_builds import queue_generator_build, save_standard_solution
from minioj.worker.main import (
    claim_next_submission,
    claim_next_testcase_build,
    judge_submission,
    process_testcase_build,
)


def setup_history(client):
    admin_id, token = create_user("HistoryAdmin", role="admin")
    problem = create_problem(admin_id, "mutable-case")
    with SessionLocal() as db:
        add_testcase(db, db.get(Problem, problem.id), "hidden", "1\n", "1\n")
    response = client.post(
        "/api/v1/submissions",
        headers=bearer(token),
        json={
            "problem_id": problem.id,
            "source_code": "int main(){}",
            "language": "cpp20",
        },
    )
    assert response.status_code == 202
    return admin_id, token, problem.id, response.json()["submission_id"]


def test_internal_revisions_remain_without_page_warnings_and_noop_is_stable(client):
    _, token, pid, sid = setup_history(client)
    login(client, "HistoryAdmin")
    assert (
        "This problem has been modified since"
        not in client.get(f"/submissions/{sid}").text
    )
    headers = bearer(token)
    assert (
        client.put(
            f"/api/v1/admin/problems/{pid}", headers=headers, json=problem_payload(pid)
        ).status_code
        == 200
    )
    assert (
        "This problem has been modified since"
        not in client.get(f"/submissions/{sid}").text
    )
    assert "This problem has been modified since" not in client.get("/submissions").text
    assert "This problem has been modified" not in client.get(f"/problems/{pid}").text
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert row.problem_revision != row.problem.revision
    fresh = client.post(
        "/api/v1/submissions",
        headers=headers,
        json={"problem_id": pid, "source_code": "int main(){}"},
    ).json()["submission_id"]
    assert (
        "This problem has been modified since"
        not in client.get(f"/submissions/{fresh}").text
    )
    # Resaving the same problem must not invalidate a fresh submission.
    assert (
        client.put(
            f"/api/v1/admin/problems/{pid}", headers=headers, json=problem_payload(pid)
        ).status_code
        == 200
    )
    assert (
        "This problem has been modified since"
        not in client.get(f"/submissions/{fresh}").text
    )
    with SessionLocal() as db:
        row = db.get(Submission, fresh)
        assert row.problem_revision == row.problem.revision
        update_testcase(
            db, row.problem, db.query(CaseModel).one().id, "hidden", output_data="2\n"
        )
    assert (
        "This problem has been modified since"
        not in client.get(f"/submissions/{fresh}").text
    )
    with SessionLocal() as db:
        row = db.get(Submission, fresh)
        assert row.problem_revision != row.problem.revision


def test_web_edit_and_delete_keep_results_and_hide_problem_everywhere(client):
    _, token, pid, sid = setup_history(client)
    _, stranger = create_user("Stranger")
    with SessionLocal() as db:
        submission = db.get(Submission, sid)
        submission.status = "FINISHED"
        submission.verdict = "AC"
        submission.judge_result = json.dumps(
            {"verdict": "AC", "summary": "Original result"}
        )
        db.commit()
    login(client, "HistoryAdmin")
    editor = client.get(f"/admin/problems/{pid}/edit")
    assert "locked because" not in editor.text
    fields = problem_payload(pid)
    fields["csrf_token"] = csrf(editor.text)
    assert (
        client.post(
            f"/admin/problems/{pid}/edit", data=fields, follow_redirects=False
        ).status_code
        == 303
    )
    assert "modified since" not in client.get(f"/submissions/{sid}").text
    assert (
        client.post(
            f"/admin/problems/{pid}/delete",
            data={"csrf_token": fields["csrf_token"]},
            follow_redirects=False,
        ).status_code
        == 303
    )
    for path in ("/", "/problems", "/admin", "/api/v1/problems"):
        page = client.get(path)
        assert page.status_code == 200
        # Admin recent submissions still reference the problem by ID.
        assert f"/problems/{pid}/edit" not in page.text
        if path != "/admin":
            assert pid not in page.text
    tombstone = client.get(f"/problems/{pid}")
    assert tombstone.status_code == 410
    assert "Problem not found" in tombstone.text
    assert "The requested problem does not exist." in tombstone.text
    assert "Updated statement" not in tombstone.text
    for path in (
        f"/api/v1/problems/{pid}",
        f"/api/v1/agent/problems/{pid}",
        f"/admin/problems/{pid}/edit",
    ):
        assert client.get(path, headers=bearer(token)).status_code == 404
    assert (
        client.post(
            "/api/v1/submissions",
            headers=bearer(token),
            json={"problem_id": pid, "source_code": "int main(){}"},
        ).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/admin/problems/{pid}",
            headers=bearer(token),
            json=problem_payload(pid),
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/admin/problems", headers=bearer(token), json=problem_payload(pid)
        ).status_code
        == 409
    )
    detail = client.get(f"/submissions/{sid}")
    assert "This problem has been deleted" not in detail.text
    assert "Accepted." in detail.text
    assert (
        client.get(f"/api/v1/submissions/{sid}", headers=bearer(token)).json()[
            "verdict"
        ]
        == "AC"
    )
    assert (
        client.get(
            f"/api/v1/agent/submissions/{sid}/feedback", headers=bearer(token)
        ).json()["summary"]
        == "Accepted."
    )
    with SessionLocal() as db:
        # Exposure is sanitized, but editing/deleting never rewrites saved results.
        assert (
            json.loads(db.get(Submission, sid).judge_result)["summary"]
            == "Original result"
        )
    assert (
        client.get(f"/api/v1/submissions/{sid}", headers=bearer(stranger)).status_code
        == 404
    )


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_queue_never_judges_replacement_or_deleted_problem(client, monkeypatch, action):
    _, token, pid, sid = setup_history(client)
    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.judge",
        lambda *a, **k: pytest.fail("stale job executed"),
    )
    if action == "edit":
        client.put(
            f"/api/v1/admin/problems/{pid}",
            headers=bearer(token),
            json=problem_payload(pid),
        )
        assert claim_next_submission() == sid
        judge_submission(sid)
    else:
        client.delete(f"/api/v1/admin/problems/{pid}", headers=bearer(token))
        assert claim_next_submission() is None
    with SessionLocal() as db:
        submission = db.get(Submission, sid)
        assert submission.status == "FINISHED"
        assert submission.verdict == "IE"
        assert (
            "modified" if action == "edit" else "deleted"
        ) in submission.judge_result


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_running_judge_keeps_captured_tests_when_problem_changes(
    client, monkeypatch, action
):
    _, _, pid, sid = setup_history(client)

    def judge(_judge, source, tests, time_ms, memory_mb, on_compiled):
        assert tests == [("1\n", "1\n")]
        with SessionLocal() as db:
            problem = db.get(Problem, pid)
            if action == "delete":
                delete_problem(db, problem)
            else:
                update_testcase(
                    db,
                    problem,
                    db.query(CaseModel).one().id,
                    "hidden",
                    output_data="2\n",
                )
        on_compiled()
        return {"success": True}, {
            "verdict": "AC",
            "summary": "Captured version accepted",
        }

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", judge)
    assert claim_next_submission() == sid
    judge_submission(sid)
    with SessionLocal() as db:
        submission = db.get(Submission, sid)
        assert submission.verdict == "AC"
        if action == "edit":
            assert submission.problem_revision != submission.problem.revision
        else:
            assert submission.problem.deleted_at is not None


@pytest.mark.parametrize("action", ["edit", "delete"])
def test_generator_cannot_publish_after_problem_changes(client, monkeypatch, action):
    admin_id, _, pid, _ = setup_history(client)
    with SessionLocal() as db:
        problem = db.get(Problem, pid)
        save_standard_solution(db, problem, b"int main(){}")
        build = queue_generator_build(
            db, problem, db.get(User, admin_id), b"int main(){}", 1, 1
        )
        bid = build.id

    def generate(*args, **kwargs):
        with SessionLocal() as db:
            problem = db.get(Problem, pid)
            if action == "delete":
                delete_problem(db, problem)
            else:
                save_standard_solution(db, problem, b"int main(){return 1;}")
        return [("NEW GENERATED INPUT", "1\n")]

    monkeypatch.setattr("minioj.worker.main.DockerJudge.build_testcases", generate)
    assert claim_next_testcase_build() == bid
    process_testcase_build(bid)
    with SessionLocal() as db:
        build = db.get(BuildModel, bid)
        assert build.status == "FAILED"
        assert build.created_count == 0
        assert ("modified" if action == "edit" else "deleted") in build.error
        assert db.query(CaseModel).count() == 1


def test_multiple_queued_generator_batches_can_append_to_same_problem(
    client, monkeypatch
):
    admin_id, _, pid, _ = setup_history(client)
    with SessionLocal() as db:
        problem = db.get(Problem, pid)
        save_standard_solution(db, problem, b"int main(){}")
        for seed in (1, 2):
            queue_generator_build(
                db, problem, db.get(User, admin_id), b"int main(){}", 1, seed
            )
    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases",
        lambda *a, **kw: [(str(kw["base_seed"]), "answer")],
    )
    for _ in range(2):
        bid = claim_next_testcase_build()
        assert bid is not None
        process_testcase_build(bid)
    with SessionLocal() as db:
        assert [row.status for row in db.query(BuildModel).all()] == [
            "FINISHED",
            "FINISHED",
        ]
        assert db.query(CaseModel).count() == 3


def test_stale_generator_is_rejected_before_docker_starts(client, monkeypatch):
    admin_id, _, pid, _ = setup_history(client)
    with SessionLocal() as db:
        problem = db.get(Problem, pid)
        save_standard_solution(db, problem, b"int main(){}")
        bid = queue_generator_build(
            db, problem, db.get(User, admin_id), b"int main(){}", 1, 1
        ).id
        save_standard_solution(db, problem, b"int main(){return 1;}")
    monkeypatch.setattr(
        "minioj.worker.main.DockerJudge.build_testcases",
        lambda *a, **kw: pytest.fail("stale generator executed"),
    )
    assert claim_next_testcase_build() == bid
    process_testcase_build(bid)
    with SessionLocal() as db:
        assert db.get(BuildModel, bid).status == "FAILED"
        assert "modified" in db.get(BuildModel, bid).error


def test_failed_edits_and_deletes_do_not_change_revision_or_queued_submission(
    client, monkeypatch
):
    _, _, pid, sid = setup_history(client)
    with SessionLocal() as db:
        problem = db.get(Problem, pid)
        revision, title = problem.revision, problem.title

        def fail_commit():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="commit failed"):
            update_problem(db, problem, {"title": "Should roll back"})
        with pytest.raises(RuntimeError, match="commit failed"):
            delete_problem(db, problem)
    with SessionLocal() as db:
        problem = db.get(Problem, pid)
        assert (problem.revision, problem.title, problem.deleted_at) == (
            revision,
            title,
            None,
        )
        submission = db.get(Submission, sid)
        assert submission.status == "QUEUED"
        assert submission.problem_revision == problem.revision
