from sqlalchemy import create_engine, inspect

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
