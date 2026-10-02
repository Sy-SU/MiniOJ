from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_phase1 import bearer, create_problem, create_user, csrf, login

from minioj.config import settings
from minioj.contests import contest_status, problem_label, save_contest, standings
from minioj.database import SessionLocal, init_db
from minioj.models import (
    Contest,
    ContestParticipant,
    Problem,
    SchemaMigration,
    Submission,
    SubmissionJudgeRun,
    User,
)
from minioj.problems import add_testcase, delete_problem
from minioj.profiles import profile_statistics
from minioj.submissions import rejudge_submission, sync_judge_run
from minioj.worker.main import (
    claim_next_submission,
    finish_with_ie,
    judge_submission,
    recover_interrupted_work,
)


@pytest.fixture
def feature_data(client):
    users = {
        role: create_user(name, role=role)
        for role, name in [
            ("system", "SysOwner"),
            ("admin", "ContentMgr"),
            ("user", "Contestant"),
        ]
    }
    create_problem(users["admin"][0], "feature-one")
    with SessionLocal() as db:
        problem = db.get(Problem, "feature-one")
        add_testcase(db, problem, "hidden", "secret-input", "answer")
    return users


def make_submission(
    data, *, verdict="WA", status="FINISHED", contest_id=None, created_at=None
):
    with SessionLocal() as db:
        row = Submission(
            user_id=data["user"][0],
            problem_id="feature-one",
            source_code="int main(){}",
            status=status,
            verdict=verdict if status == "FINISHED" else None,
            contest_id=contest_id,
            created_at=created_at or datetime.now(UTC),
            finished_at=datetime.now(UTC) if status == "FINISHED" else None,
            compile_result=json.dumps(
                {"success": verdict != "CE", "stderr": "compiler message"}
            )
            if status == "FINISHED"
            else None,
            judge_result=json.dumps(
                {
                    "verdict": verdict,
                    "summary": "Judged.",
                    "resources": {"time_ms": 2, "memory_kb": 1024},
                    "failure": {
                        "test_index": 1,
                        "is_sample": False,
                        "input": "secret-input",
                        "expected": "secret-output",
                        "actual": "wrong",
                    },
                }
            )
            if status == "FINISHED"
            else None,
        )
        db.add(row)
        db.commit()
        return row.id


def test_roles_migrate_once_without_promoting_new_admins(client):
    with SessionLocal() as db:
        marker = db.get(SchemaMigration, "roles-v2")
        db.delete(marker)
        db.commit()
    legacy, token = create_user("Legacy", role="admin")
    init_db()
    assert client.get("/api/v1/me", headers=bearer(token)).json()["role"] == "system"
    new, _ = create_user("NewAdmin", role="admin")
    init_db()
    with SessionLocal() as db:
        assert db.get(User, legacy).role == "system"
        assert db.get(User, new).role == "admin"
        assert db.get(SchemaMigration, "roles-v2") is not None


@pytest.mark.parametrize("role", ["user", "admin", "system"])
@pytest.mark.parametrize(
    "path",
    [
        "/manage",
        "/manage/problems",
        "/manage/submissions",
        "/manage/contests",
        "/manage/users",
        "/manage/system",
    ],
)
def test_management_backend_permissions(client, feature_data, role, path):
    login(
        client,
        {"user": "Contestant", "admin": "ContentMgr", "system": "SysOwner"}[role],
    )
    expected = (
        403
        if role == "user"
        or (role == "admin" and path in {"/manage/users", "/manage/system"})
        else 200
    )
    response = client.get(path)
    assert response.status_code == expected
    if expected == 200:
        assert (
            "Management Console" in response.text and "console-sidebar" in response.text
        )
        assert ('href="/manage/users"' in response.text) == (role == "system")


@pytest.mark.parametrize("role", ["user", "admin", "system"])
def test_navigation_and_legacy_redirect(client, feature_data, role):
    login(
        client,
        {"user": "Contestant", "admin": "ContentMgr", "system": "SysOwner"}[role],
    )
    html = client.get("/problems").text
    assert ('class="management-entry"' in html) == (role != "user")
    if role != "user":
        assert (
            html.index('class="brand"')
            < html.index('class="management-entry"')
            < html.index('class="nav-links"')
        )
        response = client.get("/admin", follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/manage"
    else:
        assert client.get("/admin").status_code == 403


def test_system_user_access_and_self_protection(client, feature_data):
    target_id = feature_data["user"][0]
    login(client, "ContentMgr")
    assert (
        client.post(
            f"/manage/users/{target_id}", data={"role": "admin", "is_active": "on"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/admin/users/{target_id}", data={"role": "system", "is_active": "on"}
        ).status_code
        == 403
    )
    login(client, "SysOwner")
    token = csrf(client.get("/manage/users").text)
    assert (
        client.post(
            f"/manage/users/{target_id}", data={"role": "admin", "is_active": "on"}
        ).status_code
        == 403
    )
    for role, active in [("admin", "on"), ("user", "on"), ("user", "")]:
        response = client.post(
            f"/manage/users/{target_id}",
            data={"csrf_token": token, "role": role, "is_active": active},
        )
        assert response.status_code == 200
    system_id = feature_data["system"][0]
    assert (
        client.post(
            f"/manage/users/{system_id}",
            data={"csrf_token": token, "role": "admin", "is_active": "on"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/manage/users/{system_id}", data={"csrf_token": token, "role": "system"}
        ).status_code
        == 409
    )
    assert settings.secret_key not in client.get("/manage/system").text


@pytest.mark.parametrize("role", ["user", "admin", "system"])
def test_rejudge_permission_and_identity_preservation(client, feature_data, role):
    sid = make_submission(feature_data)
    response = client.post(
        f"/api/v1/manage/submissions/{sid}/rejudge",
        headers=bearer(feature_data[role][1]),
    )
    assert response.status_code == (403 if role == "user" else 202)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert db.query(Submission).count() == 1
        assert (
            row.source_code == "int main(){}" and row.user_id == feature_data["user"][0]
        )
        if role != "user":
            assert row.status == "QUEUED" and row.verdict is None
            assert (
                row.compile_result
                is row.judge_result
                is row.started_at
                is row.finished_at
                is None
            )
            runs = db.scalars(
                select(SubmissionJudgeRun)
                .where(SubmissionJudgeRun.submission_id == sid)
                .order_by(SubmissionJudgeRun.generation)
            ).all()
            assert [r.trigger_type for r in runs] == ["initial", "rejudge"]
            assert (
                runs[0].verdict == "WA"
                and runs[1].triggered_by == feature_data[role][0]
            )
            assert (
                client.get(
                    f"/api/v1/submissions/{sid}",
                    headers=bearer(feature_data["user"][1]),
                ).json()["verdict"]
                is None
            )


@pytest.mark.parametrize("state", ["QUEUED", "COMPILING", "RUNNING"])
def test_rejudge_rejects_active_state(client, feature_data, state):
    sid = make_submission(feature_data, status=state)
    assert (
        client.post(
            f"/api/v1/manage/submissions/{sid}/rejudge",
            headers=bearer(feature_data["admin"][1]),
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        assert db.query(SubmissionJudgeRun).count() == 1


def test_rejudge_session_csrf_get_and_deleted_problem(client, feature_data):
    sid = make_submission(feature_data)
    login(client, "ContentMgr")
    assert client.get(f"/manage/submissions/{sid}/rejudge").status_code == 405
    assert client.post(f"/manage/submissions/{sid}/rejudge").status_code == 403
    token = csrf(client.get(f"/manage/submissions/{sid}").text)
    with SessionLocal() as db:
        delete_problem(db, db.get(Problem, "feature-one"))
    assert (
        client.post(
            f"/manage/submissions/{sid}/rejudge", data={"csrf_token": token}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("before,after", [("WA", "AC"), ("AC", "WA"), ("CE", "AC")])
def test_worker_rejudge_updates_history_and_profile(
    client, feature_data, monkeypatch, before, after
):
    sid = make_submission(feature_data, verdict=before)
    with SessionLocal() as db:
        original = db.get(Submission, sid).created_at
        problem = db.get(Problem, "feature-one")
        problem.time_limit_ms = 4321
        problem.revision += 1
        db.commit()
    assert (
        client.post(
            f"/api/v1/manage/submissions/{sid}/rejudge",
            headers=bearer(feature_data["system"][1]),
        ).status_code
        == 202
    )
    with SessionLocal() as db:
        assert profile_statistics(db, feature_data["user"][0])["solved_count"] == 0

    def judge(_judge, source, tests, time_limit, memory_limit, **options):
        assert time_limit == 4321 and tests == [("secret-input", "answer")]
        options["on_compiled"]()
        return {"success": True}, {
            "verdict": after,
            "summary": "Result.",
            "tests": {
                "total": 1,
                "passed": int(after == "AC"),
                "failed_test": None if after == "AC" else 1,
            },
            "resources": {"time_ms": 5, "memory_kb": 2048},
        }

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", judge)
    assert claim_next_submission() == sid
    judge_submission(sid)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert (
            row.verdict == after
            and row.status == "FINISHED"
            and row.created_at == original
        )
        runs = row.judge_runs
        assert [r.verdict for r in runs] == [before, after]
        assert (
            runs[1].status == "FINISHED"
            and runs[1].finished_at
            and runs[1].compile_result
        )
        assert row.problem_revision == db.get(Problem, "feature-one").revision
        assert profile_statistics(db, feature_data["user"][0])["solved_count"] == int(
            after == "AC"
        )


def test_concurrent_rejudge_creates_one_run(client, feature_data):
    sid = make_submission(feature_data)
    barrier = Barrier(2)

    def trigger():
        with SessionLocal() as db:
            actor = db.get(User, feature_data["admin"][0])
            barrier.wait(timeout=5)
            try:
                rejudge_submission(db, sid, actor)
                return 202
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(lambda _: trigger(), range(2))) == [202, 409]
    with SessionLocal() as db:
        assert db.query(SubmissionJudgeRun).count() == 2


def test_old_worker_result_cannot_overwrite_new_generation(
    client, feature_data, monkeypatch
):
    sid = make_submission(feature_data, status="QUEUED")
    assert claim_next_submission() == sid

    def stale_judge(*args, **options):
        options["on_compiled"]()
        with SessionLocal() as db:
            row = db.get(Submission, sid)
            row.status = "FINISHED"
            row.verdict = "IE"
            row.judge_result = json.dumps({"verdict": "IE", "summary": "Interrupted"})
            db.commit()
            rejudge_submission(db, sid, db.get(User, feature_data["admin"][0]))
        assert claim_next_submission() == sid
        return {"success": True}, {"verdict": "AC", "summary": "Stale"}

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", stale_judge)
    judge_submission(sid)
    finish_with_ie(sid, "Stale IE", generation=1)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert (
            row.judge_generation == 2
            and row.status == "COMPILING"
            and row.verdict is None
        )
        assert row.judge_runs[0].verdict == "IE"
    recover_interrupted_work()
    with SessionLocal() as db:
        assert db.get(Submission, sid).judge_runs[-1].verdict == "IE"


@pytest.mark.parametrize("policy", ["full", "diagnostic", "verdict_only"])
def test_history_obeys_owner_and_feedback_policy(
    client, feature_data, monkeypatch, policy
):
    from minioj.server import management

    monkeypatch.setattr(
        management, "settings", replace(settings, feedback_policy=policy)
    )
    sid = make_submission(feature_data)
    with SessionLocal() as db:
        rid = db.get(Submission, sid).judge_runs[0].id
    create_user("OtherUser")
    login(client, "OtherUser")
    assert client.get(f"/submissions/{sid}/history/{rid}").status_code == 404
    assert client.get(f"/manage/submissions/{sid}/history/{rid}").status_code == 403
    login(client, "Contestant")
    page = client.get(f"/submissions/{sid}/history/{rid}")
    assert (
        page.status_code == 200
        and "secret-input" not in page.text
        and "secret-output" not in page.text
    )
    login(client, "ContentMgr")
    page = client.get(f"/manage/submissions/{sid}/history/{rid}")
    assert page.status_code == 200
    assert ("secret-input" in page.text) == (policy == "full")


def png_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(stream, format="PNG")
    return stream.getvalue()


def test_avatar_upload_default_and_profile_privacy(client, feature_data):
    profile = client.get("/users/contestant")
    assert profile.status_code == 200 and "Contestant@example.com" not in profile.text
    assert (
        client.get("/users/Contestant/avatar").headers["content-type"]
        == "image/svg+xml"
    )
    login(client, "Contestant")
    token = csrf(client.get("/settings").text)
    assert (
        client.post(
            "/settings/avatar", files={"avatar": ("me.png", png_bytes(), "image/png")}
        ).status_code
        == 403
    )
    response = client.post(
        "/settings/avatar",
        data={"csrf_token": token},
        files={"avatar": ("me.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200
    avatar = client.get("/users/Contestant/avatar")
    assert avatar.status_code == 200 and avatar.headers["content-type"] == "image/png"
    assert avatar.headers["x-content-type-options"] == "nosniff"
    with Image.open(io.BytesIO(avatar.content)) as image:
        assert image.size == (32, 32)
    with SessionLocal() as db:
        assert db.get(User, feature_data["user"][0]).avatar_key.endswith(".png")


@pytest.mark.parametrize(
    "filename,content,media,status",
    [
        ("bad.svg", b"<svg/>", "image/svg+xml", 422),
        ("fake.png", b"not an image", "image/png", 422),
        ("../me.png", b"image", "image/png", 422),
        ("/tmp/me.png", b"image", "image/png", 422),
        ("me.png", b"image", "image/jpeg", 422),
        ("me.png", b"x" * (1024 * 1024 + 1), "image/png", 413),
    ],
)
def test_avatar_rejects_unsafe_upload(
    client, feature_data, filename, content, media, status
):
    login(client, "Contestant")
    token = csrf(client.get("/settings").text)
    assert (
        client.post(
            "/settings/avatar",
            data={"csrf_token": token},
            files={"avatar": (filename, content, media)},
        ).status_code
        == status
    )
    with SessionLocal() as db:
        assert db.get(User, feature_data["user"][0]).avatar_key is None


def test_profile_counts_distinct_current_ac(client, feature_data):
    first = make_submission(feature_data, verdict="AC")
    second = make_submission(feature_data, verdict="AC")
    make_submission(feature_data, verdict="WA")
    with SessionLocal() as db:
        stats = profile_statistics(db, feature_data["user"][0])
        assert (
            stats["solved_count"] == 1
            and stats["accepted"] == 2
            and stats["total"] == 3
        )
        assert stats["ac_rate"] == 66.7
        rejudge_submission(db, first, db.get(User, feature_data["admin"][0]))
        assert profile_statistics(db, feature_data["user"][0])["solved_count"] == 1
        rejudge_submission(db, second, db.get(User, feature_data["system"][0]))
        assert profile_statistics(db, feature_data["user"][0])["solved_count"] == 0


def create_contest(data, *, state="RUNNING"):
    now = datetime.now(UTC).replace(microsecond=0)
    start = (
        now + timedelta(hours=1) if state == "UPCOMING" else now - timedelta(hours=1)
    )
    end = now - timedelta(minutes=1) if state == "ENDED" else start + timedelta(hours=2)
    with SessionLocal() as db:
        return save_contest(
            db,
            db.get(User, data["admin"][0]),
            "Round One",
            "Description",
            start,
            end,
            ["feature-one"],
        ).id


def test_contest_exact_time_boundaries_and_labels():
    start = datetime(2026, 10, 1, tzinfo=UTC)
    contest = Contest(start_time=start, end_time=start + timedelta(hours=1))
    assert contest_status(contest, start - timedelta(microseconds=1)) == "UPCOMING"
    assert contest_status(contest, start) == "RUNNING"
    assert contest_status(contest, contest.end_time) == "ENDED"
    assert [problem_label(n) for n in (1, 26, 27, 52, 53)] == [
        "A",
        "Z",
        "AA",
        "AZ",
        "BA",
    ]


@pytest.mark.parametrize("state", ["UPCOMING", "RUNNING", "ENDED"])
def test_contest_submission_time_and_problem_guards(client, feature_data, state):
    cid = create_contest(feature_data, state=state)
    payload = {
        "problem_id": "feature-one",
        "language": "cpp20",
        "source_code": "int main(){}",
    }
    assert (
        client.post(f"/api/v1/contests/{cid}/submissions", json=payload).status_code
        == 401
    )
    response = client.post(
        f"/api/v1/contests/{cid}/submissions",
        headers=bearer(feature_data["user"][1]),
        json=payload,
    )
    assert response.status_code == (202 if state == "RUNNING" else 409)
    page = client.get(f"/contests/{cid}")
    assert page.status_code == 200 and state in page.text
    if state == "UPCOMING":
        assert client.get(f"/contests/{cid}/problems/A").status_code == 404
    if state == "RUNNING":
        sid = response.json()["submission_id"]
        with SessionLocal() as db:
            assert db.get(Submission, sid).contest_id == cid
        payload["problem_id"] = "unknown-problem"
        assert (
            client.post(
                f"/api/v1/contests/{cid}/submissions",
                headers=bearer(feature_data["user"][1]),
                json=payload,
            ).status_code
            == 422
        )


def test_contest_crud_order_permissions_and_started_time_lock(client, feature_data):
    cid = create_contest(feature_data, state="UPCOMING")
    create_problem(feature_data["admin"][0], "feature-two")
    login(client, "Contestant")
    assert client.get(f"/manage/contests/{cid}/edit").status_code == 403
    login(client, "ContentMgr")
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        save_contest(
            db,
            db.get(User, feature_data["admin"][0]),
            "Updated",
            "",
            c.start_time,
            c.end_time,
            ["feature-two", "feature-one"],
            cid,
        )
        assert [p.problem_id for p in c.problems] == ["feature-two", "feature-one"]
        c.start_time = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
        save_contest(
            db,
            db.get(User, feature_data["admin"][0]),
            "Updated",
            "",
            c.start_time,
            c.end_time,
            ["feature-one"],
            cid,
        )
        assert [p.problem_id for p in c.problems] == ["feature-one"]
        with pytest.raises(HTTPException) as error:
            save_contest(
                db,
                db.get(User, feature_data["admin"][0]),
                "Updated",
                "",
                c.start_time,
                c.end_time + timedelta(minutes=1),
                ["feature-one"],
                cid,
            )
        assert error.value.status_code == 409
        db.rollback()
    token = csrf(client.get(f"/manage/contests/{cid}/edit").text)
    assert (
        client.post(
            f"/manage/contests/{cid}/delete", data={"csrf_token": token}
        ).status_code
        == 409
    )
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        c.end_time = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert (
        client.post(
            f"/manage/contests/{cid}/delete", data={"csrf_token": token}
        ).status_code
        == 200
    )
    assert client.get(f"/contests/{cid}").status_code == 404


def test_standing_penalty_and_rejudge_bidirectional(client, feature_data):
    cid = create_contest(feature_data)
    with SessionLocal() as db:
        start = db.get(Contest, cid).start_time
    for i, verdict in enumerate(["CE", "IE", "WA", "TLE"]):
        make_submission(
            feature_data,
            verdict=verdict,
            contest_id=cid,
            created_at=start + timedelta(minutes=i + 1),
        )
    sid = make_submission(
        feature_data,
        verdict="AC",
        contest_id=cid,
        created_at=start + timedelta(minutes=10),
    )
    make_submission(
        feature_data,
        verdict="WA",
        contest_id=cid,
        created_at=start + timedelta(minutes=20),
    )
    make_submission(
        feature_data, verdict="AC"
    )  # Practice must not change contest scores.
    with SessionLocal() as db:
        contest = db.get(Contest, cid)
        row = standings(db, contest)[0]
        assert (
            row["solved"] == 1
            and row["penalty"] == 50
            and row["problems"]["feature-one"]["wrong"] == 2
        )
        rejudge_submission(db, sid, db.get(User, feature_data["admin"][0]))
        assert standings(db, contest)[0]["solved"] == 0
        current = db.get(Submission, sid)
        current.status = "FINISHED"
        current.verdict = "WA"
        db.commit()
        assert standings(db, contest)[0]["solved"] == 0
        rejudge_submission(db, sid, db.get(User, feature_data["system"][0]))
        current.status = "FINISHED"
        current.verdict = "AC"
        current.finished_at = datetime.now(UTC)
        db.commit()
        sync_judge_run(db, sid)
        db.commit()
        assert (
            standings(db, contest)[0]["solved"] == 1
            and standings(db, contest)[0]["penalty"] == 50
        )
    assert client.get(f"/contests/{cid}/standings").status_code == 200


def test_management_submission_filters_and_empty_fields(client, feature_data):
    sid = make_submission(feature_data)
    login(client, "ContentMgr")
    response = client.get(
        "/manage/submissions",
        params={
            "submission_id": sid,
            "user": "Contestant",
            "problem": "feature-one",
            "contest": "",
            "language": "cpp20",
            "status": "FINISHED",
            "verdict": "WA",
        },
    )
    assert (
        response.status_code == 200
        and f'href="/manage/submissions/{sid}"' in response.text
    )
    assert "No submissions" in client.get("/manage/submissions?verdict=AC").text
    assert client.get("/manage/submissions?submission_id=invalid").status_code == 422


@pytest.mark.parametrize(
    "format,extension,media",
    [
        ("PNG", "png", "image/png"),
        ("JPEG", "jpg", "image/jpeg"),
        ("WEBP", "webp", "image/webp"),
    ],
)
def test_avatar_formats_reencoded_and_replacement_cleaned(
    client, feature_data, format, extension, media
):
    from minioj.avatars import avatar_path

    login(client, "Contestant")
    token = csrf(client.get("/settings").text)
    first = client.post(
        "/settings/avatar",
        data={"csrf_token": token},
        files={"avatar": ("old.png", png_bytes(), "image/png")},
    )
    assert first.status_code == 200
    with SessionLocal() as db:
        old_key = db.get(User, feature_data["user"][0]).avatar_key
    image = io.BytesIO()
    Image.new("RGB", (512, 320), "blue").save(image, format=format)
    response = client.post(
        "/settings/avatar",
        data={"csrf_token": token},
        files={"avatar": (f"new.{extension}", image.getvalue(), media)},
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        key = db.get(User, feature_data["user"][0]).avatar_key
        assert key != old_key and not avatar_path(old_key).exists()
    with Image.open(
        io.BytesIO(client.get("/users/Contestant/avatar").content)
    ) as result:
        assert result.format == "PNG" and result.size == (256, 160)


def test_avatar_pixel_limit_and_stored_path_validation(client, feature_data):
    login(client, "Contestant")
    token = csrf(client.get("/settings").text)
    image = io.BytesIO()
    Image.new("RGB", (2049, 2049), "red").save(image, format="PNG")
    assert len(image.getvalue()) < 1024 * 1024
    assert (
        client.post(
            "/settings/avatar",
            data={"csrf_token": token},
            files={"avatar": ("huge.png", image.getvalue(), "image/png")},
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        db.get(User, feature_data["user"][0]).avatar_key = "../private.png"
        db.commit()
    response = client.get("/users/Contestant/avatar")
    assert (
        response.status_code == 200
        and response.headers["content-type"] == "image/svg+xml"
    )


def test_avatar_commit_failure_cleans_only_new_file(client, feature_data, monkeypatch):
    from minioj.avatars import avatar_path

    login(client, "Contestant")
    token = csrf(client.get("/settings").text)
    files = {"avatar": ("me.png", png_bytes(), "image/png")}
    assert (
        client.post(
            "/settings/avatar", data={"csrf_token": token}, files=files
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        old_key = db.get(User, feature_data["user"][0]).avatar_key
    before = set((settings.data_dir / "avatars").iterdir())

    def fail_commit(_self):
        raise RuntimeError("Simulated database commit failure")

    monkeypatch.setattr(Session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="Simulated database"):
        client.post("/settings/avatar", data={"csrf_token": token}, files=files)
    with SessionLocal() as db:
        assert db.get(User, feature_data["user"][0]).avatar_key == old_key
    assert set((settings.data_dir / "avatars").iterdir()) == before
    assert avatar_path(old_key).is_file()


def test_rejudge_queue_full_is_atomic(client, feature_data, monkeypatch):
    monkeypatch.setattr(
        "minioj.submissions.settings", replace(settings, max_queued_submissions=1)
    )
    sid = make_submission(feature_data, verdict="AC")
    make_submission(feature_data, status="QUEUED")
    response = client.post(
        f"/api/v1/manage/submissions/{sid}/rejudge",
        headers=bearer(feature_data["admin"][1]),
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == str(settings.overload_retry_after_seconds)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert row.status == "FINISHED" and row.verdict == "AC"
        assert row.judge_generation == 1 and len(row.judge_runs) == 1


@pytest.mark.parametrize(
    "role,name",
    [("user", "Contestant"), ("admin", "ContentMgr"), ("system", "SysOwner")],
)
def test_contest_web_create_and_microsecond_edit(client, feature_data, role, name):
    login(client, name)
    token = csrf(client.get("/problems").text)
    start = datetime.now(UTC).replace(microsecond=123456) - timedelta(minutes=1)
    end = start + timedelta(hours=2)
    form = {
        "csrf_token": token,
        "title": "Precise times",
        "description": "",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "problem_ids": "feature-one",
    }
    response = client.post("/manage/contests/new", data=form, follow_redirects=False)
    assert response.status_code == (403 if role == "user" else 303)
    if role == "user":
        return
    location = response.headers["location"]
    page = client.get(location)
    assert start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] in page.text
    form["title"] = "Updated title"
    # Submit exactly the datetime-local representation retained by the editor.
    form["start_time"] = start.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    form["end_time"] = end.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    assert client.post(location, data=form, follow_redirects=False).status_code == 303
    assert "Updated title" in client.get(location).text
    cid = int(location.rsplit("/", 2)[1])
    with SessionLocal() as db:
        contest = db.get(Contest, cid)
        assert contest.start_time.replace(tzinfo=UTC) == start
        assert contest.end_time.replace(tzinfo=UTC) == end
    changed = dict(form)
    changed["start_time"] = (start + timedelta(microseconds=1)).isoformat()
    assert client.post(location, data=changed).status_code == 409
    changed["start_time"] = (start + timedelta(milliseconds=1)).isoformat()
    assert client.post(location, data=changed).status_code == 409
    form["end_time"] = form["start_time"]
    assert client.post(location, data=form).status_code == 422


@pytest.mark.parametrize("state", ["UPCOMING", "RUNNING", "ENDED"])
def test_contest_join_is_csrf_protected_and_idempotent(client, feature_data, state):
    cid = create_contest(feature_data, state=state)
    login(client, "Contestant")
    token = csrf(client.get(f"/contests/{cid}").text)
    assert client.post(f"/contests/{cid}/join").status_code == 403
    for _ in range(2):
        assert client.post(
            f"/contests/{cid}/join", data={"csrf_token": token}, follow_redirects=False
        ).status_code == (409 if state == "ENDED" else 303)
    with SessionLocal() as db:
        assert db.query(ContestParticipant).count() == int(state != "ENDED")


def test_standing_all_penalties_window_first_ac_and_ties(client, feature_data):
    cid = create_contest(feature_data)
    other_id, _ = create_user("AlphaUser")
    empty_id, _ = create_user("EmptyUser")
    with SessionLocal() as db:
        contest = db.get(Contest, cid)
        start, end = contest.start_time, contest.end_time
        db.add(ContestParticipant(contest_id=cid, user_id=empty_id))
        db.commit()
    for i, verdict in enumerate(["WA", "RE", "TLE", "MLE", "OLE", "CE", "IE"]):
        make_submission(
            feature_data,
            verdict=verdict,
            contest_id=cid,
            created_at=start + timedelta(minutes=i),
        )
    for when in [
        start - timedelta(seconds=1),
        end,
        start + timedelta(minutes=10),
        start + timedelta(minutes=111),
    ]:
        make_submission(feature_data, verdict="AC", contest_id=cid, created_at=when)
    other = {**feature_data, "user": (other_id, "unused")}
    make_submission(
        other, verdict="AC", contest_id=cid, created_at=start + timedelta(minutes=110)
    )
    with SessionLocal() as db:
        rows = standings(db, db.get(Contest, cid))
        assert [r["username"] for r in rows] == ["AlphaUser", "Contestant", "EmptyUser"]
        assert [r["rank"] for r in rows] == [1, 1, 3]
        assert [r["penalty"] for r in rows] == [110, 110, 0]
        assert rows[1]["problems"]["feature-one"]["wrong"] == 5


def test_history_includes_safe_compile_metadata_and_sanitizes_ie(client, feature_data):
    sid = make_submission(feature_data, verdict="AC")
    login(client, "Contestant")
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        row.compile_result = json.dumps(
            {
                "success": True,
                "time_ms": 7,
                "memory_kb": 123,
                "stderr": "/private/compiler",
            }
        )
        db.commit()
        rid = row.judge_runs[0].id
    page = client.get(f"/submissions/{sid}/history/{rid}")
    assert page.status_code == 200 and '"compile"' in page.text
    assert "/private/compiler" not in page.text
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        row.verdict = "IE"
        row.judge_result = json.dumps(
            {"verdict": "IE", "summary": "/private/internal/error"}
        )
        db.commit()
    page = client.get(f"/submissions/{sid}/history/{rid}")
    assert "/private/internal/error" not in page.text
    assert "The judge could not complete this run." in page.text
    another = make_submission(feature_data)
    assert client.get(f"/submissions/{another}/history/{rid}").status_code == 404
