from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from minioj import database
from minioj.database import Base
from minioj.models import (
    ApiToken,
    Contest,
    ContestParticipant,
    ContestProblem,
    Problem,
    Sample,
    SchemaMigration,
    Submission,
    SubmissionJudgeRun,
    User,
)
from minioj.models import TestCase as CaseModel


def test_legacy_schema_migration_preserves_identity_results_and_new_admin_role(
    tmp_path, monkeypatch
):
    legacy = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(database, "engine", legacy)
    monkeypatch.setattr(
        database,
        "settings",
        replace(
            database.settings,
            database_url=str(legacy.url),
            data_dir=tmp_path / "data",
            job_dir=tmp_path / "jobs",
        ),
    )
    try:
        with legacy.begin() as connection:
            connection.exec_driver_sql("""CREATE TABLE users (
                id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL, email VARCHAR(255) NOT NULL,
                password_hash VARCHAR(512) NOT NULL, role VARCHAR(16) NOT NULL, is_active BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""")
            connection.exec_driver_sql("""CREATE TABLE submissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                problem_id VARCHAR(80) NOT NULL REFERENCES problems(id), problem_revision INTEGER NOT NULL DEFAULT 1,
                language VARCHAR(20) NOT NULL, source_code TEXT NOT NULL, status VARCHAR(16) NOT NULL,
                verdict VARCHAR(8), created_at DATETIME NOT NULL, started_at DATETIME, finished_at DATETIME,
                compile_result TEXT, judge_result TEXT)""")
            connection.exec_driver_sql(
                "INSERT INTO users VALUES (7,'OldOwner','old@example.com','original-hash','admin',1,'2026-01-01','2026-01-01')"
            )
        Base.metadata.create_all(legacy)
        with Session(legacy) as db:
            db.add(
                Problem(
                    id="legacy-one",
                    title="Legacy",
                    statement="Original statement",
                    created_by=7,
                )
            )
            db.commit()
        with legacy.begin() as connection:
            connection.exec_driver_sql("""INSERT INTO submissions VALUES
                (42,7,'legacy-one',1,'cpp20','original code','FINISHED','AC',
                 '2026-01-01','2026-01-01','2026-01-01','{"success":true}','{"verdict":"AC","summary":"Accepted"}')""")
        database.init_db()
        database.init_db()
        with Session(legacy) as db:
            user = db.get(User, 7)
            assert (
                user.role == "system"
                and user.password_hash == "original-hash"
                and user.avatar_key is None
            )
            submission = db.get(Submission, 42)
            assert (
                submission.source_code == "original code" and submission.verdict == "AC"
            )
            assert submission.contest_id is None and submission.judge_generation == 1
            history = db.query(SubmissionJudgeRun).one()
            assert (
                history.submission_id == 42
                and history.trigger_type == "initial"
                and history.triggered_by == 7
            )
            assert (
                history.judge_result == submission.judge_result
                and history.compile_result == submission.compile_result
            )
            assert (
                history.created_at == submission.created_at
                and history.finished_at == submission.finished_at
            )
            db.add(
                User(
                    username="Content",
                    email="content@example.com",
                    password_hash="new-hash",
                    role="admin",
                )
            )
            db.commit()
        database.init_db()
        with Session(legacy) as db:
            assert db.query(User).filter_by(username="Content").one().role == "admin"
            assert db.query(SubmissionJudgeRun).count() == 1
    finally:
        legacy.dispose()


def test_pre_phase5_upgrade_preserves_all_business_rows_and_runtime_files(
    tmp_path, monkeypatch
):
    legacy = create_engine(f"sqlite:///{tmp_path / 'pre-phase5.db'}")
    local_data = tmp_path / "data"
    monkeypatch.setattr(database, "engine", legacy)
    monkeypatch.setattr(
        database,
        "settings",
        replace(
            database.settings,
            database_url=str(legacy.url),
            data_dir=local_data,
            job_dir=tmp_path / "jobs",
        ),
    )
    timestamp = datetime(2026, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
    files = {
        "problems/retained-v2/tests/001.in": b"original input\n",
        "problems/retained-v2/tests/001.out": b"original answer\n",
        "problems/retained-v2/assets/original.png": b"original statement asset",
        "avatars/original.png": b"original avatar",
    }
    for relative, content in files.items():
        path = local_data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    tables = (
        "users",
        "problems",
        "testcases",
        "samples",
        "api_tokens",
        "contests",
        "contest_problems",
        "contest_participants",
        "submissions",
        "submission_judge_runs",
        "schema_migrations",
    )

    def snapshot():
        with legacy.connect() as connection:
            return {
                name: [
                    dict(row)
                    for row in connection.exec_driver_sql(
                        f'SELECT * FROM "{name}" ORDER BY rowid'
                    ).mappings()
                ]
                for name in tables
            }

    try:
        Base.metadata.create_all(legacy)
        with Session(legacy) as db:
            owner = User(
                username="Retained",
                email="retained@example.com",
                password_hash="original-password-hash",
                role="admin",
                avatar_key="original.png",
                created_at=timestamp,
                updated_at=timestamp,
            )
            db.add_all([owner, SchemaMigration(name="roles-v2", applied_at=timestamp)])
            db.flush()
            problem = Problem(
                id="retained-v2",
                title="Original problem",
                statement="Original Unicode statement: 测试",
                created_by=owner.id,
                revision=3,
            )
            contest = Contest(
                title="Original contest",
                description="Original rules",
                start_time=timestamp,
                end_time=datetime(2026, 1, 2, tzinfo=UTC),
                created_by=owner.id,
            )
            db.add_all([problem, contest])
            db.flush()
            case = CaseModel(
                problem_id=problem.id,
                order=1,
                type="sample",
                input_path="problems/retained-v2/tests/001.in",
                output_path="problems/retained-v2/tests/001.out",
                created_at=timestamp,
            )
            db.add(case)
            db.flush()
            submission = Submission(
                user_id=owner.id,
                problem_id=problem.id,
                problem_revision=3,
                contest_id=contest.id,
                source_code="original source: 测试",
                status="FINISHED",
                verdict="AC",
                compile_result='{"success":true}',
                judge_result='{"verdict":"AC"}',
                created_at=timestamp,
                started_at=timestamp,
                finished_at=timestamp,
            )
            db.add_all(
                [
                    submission,
                    Sample(
                        problem_id=problem.id,
                        testcase_id=case.id,
                        input="original input\n",
                        output="original answer\n",
                    ),
                    ContestProblem(
                        contest_id=contest.id, problem_id=problem.id, position=1
                    ),
                    ContestParticipant(
                        contest_id=contest.id, user_id=owner.id, joined_at=timestamp
                    ),
                    ApiToken(
                        id="retained-token",
                        user_id=owner.id,
                        name="Original token metadata",
                        token_hash="original-token-hash",
                        token_preview="original-preview",
                        created_at=timestamp,
                        revoked_at=timestamp,
                    ),
                ]
            )
            db.commit()
            submission.judge_generation = 2
            submission.verdict = "WA"
            submission.judge_result = '{"verdict":"WA"}'
            db.add(
                SubmissionJudgeRun(
                    submission_id=submission.id,
                    generation=2,
                    problem_revision=3,
                    trigger_type="rejudge",
                    triggered_by=owner.id,
                    status="FINISHED",
                    verdict="WA",
                    compile_result=submission.compile_result,
                    judge_result=submission.judge_result,
                    created_at=timestamp,
                    started_at=timestamp,
                    finished_at=timestamp,
                )
            )
            db.commit()
        with legacy.begin() as connection:
            connection.exec_driver_sql("DROP INDEX uq_submission_user_request")
            connection.exec_driver_sql(
                "ALTER TABLE submissions DROP COLUMN idempotency_key_hash"
            )
            connection.exec_driver_sql(
                "ALTER TABLE submissions DROP COLUMN request_payload_sha256"
            )
        before = snapshot()
        assert len(before["submission_judge_runs"]) == 2
        database.init_db()
        database.init_db()
        after = snapshot()
        for row in after["submissions"]:
            assert row.pop("idempotency_key_hash") is None
            assert row.pop("request_payload_sha256") is None
        assert after == before
        assert all(
            (local_data / relative).read_bytes() == content
            for relative, content in files.items()
        )
        with legacy.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_submission_user_request'"
                ).scalar()
                == "uq_submission_user_request"
            )
    finally:
        legacy.dispose()
