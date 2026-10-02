from sqlalchemy import Integer, create_engine, inspect

from minioj import database


def test_existing_token_database_gains_nullable_preview_without_losing_data(
    tmp_path, monkeypatch
):
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(database, "engine", legacy_engine)
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE api_tokens (
                    id VARCHAR(40) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name VARCHAR(100) NOT NULL,
                    token_hash VARCHAR(64) NOT NULL UNIQUE,
                    created_at DATETIME NOT NULL,
                    last_used_at DATETIME,
                    expires_at DATETIME,
                    revoked_at DATETIME
                )
            """)
            connection.exec_driver_sql(
                "INSERT INTO api_tokens (id, user_id, name, token_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("token_legacy", 1, "legacy", "a" * 64, "2026-09-30 00:00:00"),
            )
        database.init_db()
        database.init_db()
        columns = inspect(legacy_engine).get_columns("api_tokens")
        assert sum(column["name"] == "token_preview" for column in columns) == 1
        with legacy_engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT id, token_hash, token_preview FROM api_tokens"
            ).one()
            assert tuple(row) == ("token_legacy", "a" * 64, None)
    finally:
        legacy_engine.dispose()


def test_existing_testcase_database_gains_phase_one_metadata(tmp_path, monkeypatch):
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy-testcases.db'}")
    monkeypatch.setattr(database, "engine", legacy_engine)
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE testcases (
                    id INTEGER PRIMARY KEY,
                    problem_id VARCHAR(80) NOT NULL,
                    type VARCHAR(16) NOT NULL,
                    input_path VARCHAR(1000) NOT NULL,
                    output_path VARCHAR(1000) NOT NULL,
                    "order" INTEGER NOT NULL
                )
            """)
            connection.exec_driver_sql("""
                CREATE TABLE samples (
                    id INTEGER PRIMARY KEY,
                    problem_id VARCHAR(80) NOT NULL,
                    input TEXT NOT NULL,
                    output TEXT NOT NULL,
                    "order" INTEGER NOT NULL
                )
            """)
            connection.exec_driver_sql(
                "INSERT INTO testcases "
                '(id, problem_id, type, input_path, output_path, "order") '
                "VALUES (1, 'legacy', 'sample', 'old.in', 'old.out', 1)"
            )
            connection.exec_driver_sql(
                'INSERT INTO samples (id, problem_id, input, output, "order") '
                "VALUES (1, 'legacy', 'in', 'out', 1)"
            )
        database.init_db()
        database.init_db()
        testcase_columns = {
            column["name"] for column in inspect(legacy_engine).get_columns("testcases")
        }
        sample_columns = {
            column["name"] for column in inspect(legacy_engine).get_columns("samples")
        }
        assert {"input_sha256", "output_sha256", "created_at"} <= testcase_columns
        assert "testcase_id" in sample_columns
        with legacy_engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT id, input_sha256, output_sha256, created_at FROM testcases"
            ).one()
            assert row[0] == 1
            assert row[1:3] == (None, None)
            assert row[3] is not None
            sample = connection.exec_driver_sql(
                "SELECT id, input, output, testcase_id FROM samples"
            ).one()
            assert tuple(sample) == (1, "in", "out", None)
    finally:
        legacy_engine.dispose()


def test_existing_problem_database_gains_standard_solution_metadata(
    tmp_path, monkeypatch
):
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy-problems.db'}")
    monkeypatch.setattr(database, "engine", legacy_engine)
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE problems (id VARCHAR(80) PRIMARY KEY)"
            )
            connection.exec_driver_sql("INSERT INTO problems (id) VALUES ('legacy')")
        database.init_db()
        database.init_db()
        problem_columns = {
            column["name"] for column in inspect(legacy_engine).get_columns("problems")
        }
        assert {
            "standard_source",
            "standard_sha256",
            "standard_updated_at",
            "revision",
            "deleted_at",
            "checker",
            "checker_name",
            "checker_bundle",
            "checker_sha256",
        } <= problem_columns
        assert inspect(legacy_engine).has_table("testcase_builds")
        with legacy_engine.connect() as connection:
            assert tuple(
                connection.exec_driver_sql(
                    "SELECT id, revision, deleted_at FROM problems"
                ).one()
            ) == ("legacy", 1, None)
            assert (
                connection.exec_driver_sql("SELECT checker FROM problems").scalar()
                == "lines"
            )
            assert tuple(
                connection.exec_driver_sql(
                    "SELECT checker_name, checker_bundle, checker_sha256 FROM problems"
                ).one()
            ) == (None, None, None)
    finally:
        legacy_engine.dispose()


def test_existing_submission_ids_are_migrated_to_autoincrement_integers(
    tmp_path, monkeypatch
):
    legacy_engine = create_engine(f"sqlite:///{tmp_path / 'legacy-submissions.db'}")
    monkeypatch.setattr(database, "engine", legacy_engine)
    try:
        with legacy_engine.begin() as connection:
            connection.exec_driver_sql("""
                CREATE TABLE submissions (
                    id VARCHAR(40) PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    problem_id VARCHAR(80) NOT NULL,
                    language VARCHAR(20) NOT NULL,
                    source_code TEXT NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    verdict VARCHAR(8),
                    created_at DATETIME NOT NULL,
                    started_at DATETIME,
                    finished_at DATETIME,
                    compile_result TEXT,
                    judge_result TEXT
                )
            """)
            connection.exec_driver_sql(
                "INSERT INTO submissions ("
                "id, user_id, problem_id, language, source_code, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "sub_later",
                    1,
                    "legacy",
                    "cpp20",
                    "later",
                    "QUEUED",
                    "2026-09-30 00:00:02",
                ),
            )
            connection.exec_driver_sql(
                "INSERT INTO submissions ("
                "id, user_id, problem_id, language, source_code, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "sub_earlier",
                    1,
                    "legacy",
                    "cpp20",
                    "earlier",
                    "FINISHED",
                    "2026-09-30 00:00:01",
                ),
            )

        database.init_db()
        database.init_db()

        columns = inspect(legacy_engine).get_columns("submissions")
        id_column = next(column for column in columns if column["name"] == "id")
        assert isinstance(id_column["type"], Integer)
        with legacy_engine.begin() as connection:
            rows = connection.exec_driver_sql(
                "SELECT id, source_code, problem_revision FROM submissions ORDER BY id"
            ).all()
            assert [tuple(row) for row in rows] == [(1, "earlier", 1), (2, "later", 1)]
            result = connection.exec_driver_sql(
                "INSERT INTO submissions ("
                "user_id, problem_id, language, source_code, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    1,
                    "legacy",
                    "cpp20",
                    "next",
                    "QUEUED",
                    "2026-09-30 00:00:03",
                ),
            )
            assert result.lastrowid == 3
    finally:
        legacy_engine.dispose()
