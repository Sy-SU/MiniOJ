"""Isolated real Docker validation of Contest, Rejudge, JudgeRun and Profile."""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

SOURCE = '#include <iostream>\nint main(){std::cout << "42\\n";}\n'


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="minioj-management-smoke-") as temporary:
        root = Path(temporary)
        os.environ.update(
            MINIOJ_DATABASE_URL=f"sqlite:///{root / 'oj.db'}",
            MINIOJ_DATA_DIR=str(root / "data"),
            MINIOJ_JOB_DIR=str(root / "jobs"),
            MINIOJ_SECRET_KEY="management-isolated-smoke-secret-key",
            MINIOJ_WORKER_OWNER=f"management-smoke-{os.getpid()}",
        )
        from fastapi.testclient import TestClient

        from minioj.contests import save_contest, standings
        from minioj.database import SessionLocal, init_db
        from minioj.judge import DockerJudge, runner
        from minioj.models import ApiToken, Contest, Problem, Submission, User
        from minioj.problems import add_testcase, update_testcase
        from minioj.profiles import profile_statistics
        from minioj.security import create_api_token, hash_password
        from minioj.server.main import app
        from minioj.worker.main import (
            claim_next_submission,
            exclusive_worker,
            judge_submission,
        )

        init_db()
        tokens, ids = {}, {}
        with SessionLocal() as db:
            for role, name in [
                ("system", "SmokeSys"),
                ("admin", "SmokeAdmin"),
                ("user", "SmokeUser"),
            ]:
                user = User(
                    username=name,
                    email=f"{name}@example.com",
                    password_hash=hash_password("management-smoke-password"),
                    role=role,
                )
                db.add(user)
                db.flush()
                tid, raw, digest, expires = create_api_token()
                db.add(
                    ApiToken(
                        id=tid,
                        user_id=user.id,
                        name="smoke",
                        token_hash=digest,
                        expires_at=expires,
                    )
                )
                tokens[role], ids[role] = raw, user.id
            problem = Problem(
                id="management-one",
                title="Print 42",
                statement="Print 42.",
                created_by=ids["admin"],
            )
            db.add(problem)
            db.commit()
            case = add_testcase(db, problem, "hidden", "private-input\n", "43\n")
            case_id = case.id
            now = datetime.now(UTC)
            contest = save_contest(
                db,
                db.get(User, ids["admin"]),
                "Docker Round",
                "",
                now - timedelta(minutes=5),
                now + timedelta(hours=1),
                [problem.id],
            )
            contest_id = contest.id

        def auth(role):
            return {"Authorization": f"Bearer {tokens[role]}"}

        def judge(sid, verdict):
            assert claim_next_submission() == sid
            judge_submission(sid)
            with SessionLocal() as db:
                row = db.get(Submission, sid)
                assert (row.status, row.verdict) == ("FINISHED", verdict), (
                    row.judge_result
                )
                assert row.judge_runs[-1].verdict == verdict
                return row.created_at

        def expected_output(value):
            with SessionLocal() as db:
                row = db.get(Problem, "management-one")
                case = next(c for c in row.testcases if c.id == case_id)
                update_testcase(db, row, case.id, case.type, output_data=value)

        def check_scores(expected):
            with SessionLocal() as db:
                row = standings(db, db.get(Contest, contest_id))[0]
                assert row["solved"] == expected
                assert row["performance"] == (1600 if expected else 800)
                assert profile_statistics(db, ids["user"])["solved_count"] == expected
            response = client.get(
                f"{app.root_path}/api/v1/contests/{contest_id}/standings"
            )
            assert response.status_code == 200 and response.json()["rows"][0] == row

        docker = DockerJudge(owner=os.environ["MINIOJ_WORKER_OWNER"])
        with exclusive_worker(), TestClient(app) as client:
            docker.ensure_available()
            try:
                for prefix in ("", "/minioj"):
                    app.root_path = prefix
                    response = client.post(
                        f"{prefix}/api/v1/contests/{contest_id}/submissions",
                        headers=auth("user"),
                        json={
                            "problem_id": "management-one",
                            "language": "cpp20",
                            "source_code": SOURCE,
                        },
                    )
                    assert response.status_code == 202, response.text
                    sid = response.json()["submission_id"]
                    original = judge(sid, "WA")
                    assert (
                        client.post(
                            f"{prefix}/api/v1/manage/submissions/{sid}/rejudge",
                            headers=auth("user"),
                        ).status_code
                        == 403
                    )
                    for output, verdict, actor in [
                        ("42\n", "AC", "admin"),
                        ("43\n", "WA", "system"),
                        ("42\n", "AC", "system"),
                    ]:
                        expected_output(output)
                        assert (
                            client.post(
                                f"{prefix}/api/v1/manage/submissions/{sid}/rejudge",
                                headers=auth(actor),
                            ).status_code
                            == 202
                        )
                        assert (
                            client.post(
                                f"{prefix}/api/v1/manage/submissions/{sid}/rejudge",
                                headers=auth(actor),
                            ).status_code
                            == 409
                        )
                        assert judge(sid, verdict) == original
                        # The first prefix verifies both score directions without another AC.
                        if not prefix:
                            check_scores(int(verdict == "AC"))
                    with SessionLocal() as db:
                        row = db.get(Submission, sid)
                        assert (
                            row.source_code == SOURCE and row.contest_id == contest_id
                        )
                        assert [run.verdict for run in row.judge_runs] == [
                            "WA",
                            "AC",
                            "WA",
                            "AC",
                        ]
                        assert [run.trigger_type for run in row.judge_runs] == [
                            "initial",
                            "rejudge",
                            "rejudge",
                            "rejudge",
                        ]
                        assert row.judge_runs[1].triggered_by == ids["admin"]
                    feedback = client.get(
                        f"{prefix}/api/v1/agent/submissions/{sid}/feedback",
                        headers=auth("user"),
                    )
                    assert (
                        feedback.status_code == 200
                        and feedback.json()["verdict"] == "AC"
                    )
                    # Restore initial mismatch so the next prefix repeats the same real transitions.
                    expected_output("43\n")
                    print(
                        f"Docker Contest/Rejudge passed at {prefix or '/'}: WA → AC → WA → AC, immutable submission, four histories."
                    )

                # Real judged ACs must survive live association edits, but only
                # the current problem list contributes to standings/performance.
                with SessionLocal() as db:
                    db.add(
                        Problem(
                            id="management-extra",
                            title="Extra",
                            statement="Extra",
                            rating=2000,
                            created_by=ids["admin"],
                        )
                    )
                    db.commit()
                    contest = db.get(Contest, contest_id)
                    actor = db.get(User, ids["admin"])
                    previous = [
                        (
                            s.id,
                            s.verdict,
                            s.created_at,
                            s.source_code,
                            len(s.judge_runs),
                        )
                        for s in db.query(Submission).all()
                    ]
                    for problem_ids, solved in [
                        (["management-extra", "management-one"], 1),
                        (["management-extra"], 0),
                        (["management-one", "management-extra"], 1),
                        (["management-one"], 1),
                    ]:
                        save_contest(
                            db,
                            actor,
                            contest.title,
                            contest.description,
                            contest.start_time,
                            contest.end_time,
                            problem_ids,
                            contest_id,
                        )
                        row = standings(db, contest)[0]
                        assert (
                            list(row["problems"]) == problem_ids
                            and row["solved"] == solved
                        )
                        if len(problem_ids) == 2:
                            assert 800 < row["performance"] < 1600
                        else:
                            assert row["performance"] == 1600
                        api = client.get(
                            f"/minioj/api/v1/contests/{contest_id}/standings"
                        )
                        assert api.status_code == 200 and api.json()["rows"][0] == row
                    assert [
                        (
                            s.id,
                            s.verdict,
                            s.created_at,
                            s.source_code,
                            len(s.judge_runs),
                        )
                        for s in db.query(Submission).all()
                    ] == previous
                    assert profile_statistics(db, ids["user"])["solved_count"] == 1
                print(
                    "Docker Contest Performance and live add/remove/reorder/readd passed; submissions and histories retained."
                )
                expected_output("42\n")
                original_settings = runner.settings
                runner.settings = replace(original_settings, compile_time_limit_ms=1)
                try:
                    response = client.post(
                        "/minioj/api/v1/submissions",
                        headers=auth("user"),
                        json={
                            "problem_id": "management-one",
                            "language": "cpp20",
                            "source_code": SOURCE,
                        },
                    )
                    assert response.status_code == 202, response.text
                    ce_sid = response.json()["submission_id"]
                    judge(ce_sid, "CE")
                finally:
                    runner.settings = original_settings
                assert (
                    client.post(
                        f"/minioj/api/v1/manage/submissions/{ce_sid}/rejudge",
                        headers=auth("admin"),
                    ).status_code
                    == 202
                )
                judge(ce_sid, "AC")
                with SessionLocal() as db:
                    assert [
                        r.verdict for r in db.get(Submission, ce_sid).judge_runs
                    ] == ["CE", "AC"]
                    assert db.query(Submission).count() == 3
                    assert profile_statistics(db, ids["user"])["solved_count"] == 1
                print(
                    "Docker compiler-budget recovery passed: CE → Rejudge → AC; practice and contest share the Worker."
                )
            finally:
                app.root_path = ""
                docker.cleanup_owned_containers()
                leftovers = [
                    p.name for p in root.joinpath("jobs").iterdir() if p.is_dir()
                ]
                assert not leftovers, leftovers
        print(
            "Management Docker smoke passed; isolated database/data/job directories and owned containers cleaned."
        )


if __name__ == "__main__":
    main()
