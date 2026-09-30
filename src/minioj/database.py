from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import Integer, create_engine, event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from minioj.config import settings


class Base(DeclarativeBase):
    pass


connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)
engine = create_engine(settings.database_url, connect_args=connect_args)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(
        dbapi_connection: object, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _upgrade_submission_ids(connection: Connection) -> None:
    columns = inspect(connection).get_columns("submissions")
    id_column = next(column for column in columns if column["name"] == "id")
    if isinstance(id_column["type"], Integer):
        return
    if connection.dialect.name != "sqlite":
        raise RuntimeError(
            "Submission IDs require an integer primary key. Automatic migration "
            "is currently supported only for SQLite."
        )

    connection.exec_driver_sql("DROP TABLE IF EXISTS submissions_integer_ids")
    connection.exec_driver_sql("""
        CREATE TABLE submissions_integer_ids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users (id),
            problem_id VARCHAR(80) NOT NULL REFERENCES problems (id),
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
    connection.exec_driver_sql("""
        INSERT INTO submissions_integer_ids (
            id,
            user_id,
            problem_id,
            language,
            source_code,
            status,
            verdict,
            created_at,
            started_at,
            finished_at,
            compile_result,
            judge_result
        )
        SELECT
            row_number() OVER (ORDER BY created_at, id),
            user_id,
            problem_id,
            language,
            source_code,
            status,
            verdict,
            created_at,
            started_at,
            finished_at,
            compile_result,
            judge_result
        FROM submissions
        ORDER BY created_at, id
    """)
    connection.exec_driver_sql("DROP TABLE submissions")
    connection.exec_driver_sql(
        "ALTER TABLE submissions_integer_ids RENAME TO submissions"
    )
    for column in ("user_id", "problem_id", "status", "verdict", "created_at"):
        connection.exec_driver_sql(
            f"CREATE INDEX ix_submissions_{column} ON submissions ({column})"
        )


def init_db() -> None:
    from minioj import models  # noqa: F401

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    settings.problems_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///"):
        db_path = settings.database_url.removeprefix("sqlite:///")
        if db_path != ":memory:":
            from pathlib import Path

            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)
    # create_all does not add columns to databases created by earlier versions.
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            # Serialize schema checks when server and worker start together.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        conflicts = (
            connection.exec_driver_sql(
                "SELECT lower(username) FROM users "
                "GROUP BY lower(username) HAVING count(*) > 1"
            )
            .scalars()
            .all()
        )
        if conflicts:
            raise RuntimeError(
                "Case-insensitive username conflicts: "
                + ", ".join(conflicts)
                + ". Resolve duplicate accounts before starting MiniOJ; "
                "no accounts were merged or renamed."
            )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_lower "
            "ON users (lower(username))"
        )
        columns = inspect(connection).get_columns("api_tokens")
        if "token_preview" not in {column["name"] for column in columns}:
            connection.exec_driver_sql(
                "ALTER TABLE api_tokens ADD COLUMN token_preview VARCHAR(19)"
            )
        problem_columns = {
            column["name"] for column in inspect(connection).get_columns("problems")
        }
        if "standard_source" not in problem_columns:
            connection.exec_driver_sql(
                "ALTER TABLE problems ADD COLUMN standard_source TEXT"
            )
        if "standard_sha256" not in problem_columns:
            connection.exec_driver_sql(
                "ALTER TABLE problems ADD COLUMN standard_sha256 VARCHAR(64)"
            )
        if "standard_updated_at" not in problem_columns:
            connection.exec_driver_sql(
                "ALTER TABLE problems ADD COLUMN standard_updated_at DATETIME"
            )
        if "revision" not in problem_columns:
            connection.exec_driver_sql(
                "ALTER TABLE problems ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )
        if "deleted_at" not in problem_columns:
            connection.exec_driver_sql(
                "ALTER TABLE problems ADD COLUMN deleted_at DATETIME"
            )
        testcase_columns = {
            column["name"] for column in inspect(connection).get_columns("testcases")
        }
        if "input_sha256" not in testcase_columns:
            connection.exec_driver_sql(
                "ALTER TABLE testcases ADD COLUMN input_sha256 VARCHAR(64)"
            )
        if "output_sha256" not in testcase_columns:
            connection.exec_driver_sql(
                "ALTER TABLE testcases ADD COLUMN output_sha256 VARCHAR(64)"
            )
        if "created_at" not in testcase_columns:
            connection.exec_driver_sql(
                "ALTER TABLE testcases ADD COLUMN created_at DATETIME"
            )
        connection.exec_driver_sql(
            "UPDATE testcases SET created_at = CURRENT_TIMESTAMP "
            "WHERE created_at IS NULL"
        )
        sample_columns = {
            column["name"] for column in inspect(connection).get_columns("samples")
        }
        if "testcase_id" not in sample_columns:
            connection.exec_driver_sql(
                "ALTER TABLE samples ADD COLUMN testcase_id INTEGER "
                "REFERENCES testcases(id) ON DELETE SET NULL"
            )
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_samples_testcase_id "
            "ON samples (testcase_id) WHERE testcase_id IS NOT NULL"
        )
        _upgrade_submission_ids(connection)
        submission_columns = {
            column["name"] for column in inspect(connection).get_columns("submissions")
        }
        if "problem_revision" not in submission_columns:
            connection.exec_driver_sql(
                "ALTER TABLE submissions ADD COLUMN problem_revision INTEGER NOT NULL DEFAULT 1"
            )
