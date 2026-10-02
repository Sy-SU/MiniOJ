from __future__ import annotations

import sqlite3

import pytest

from scripts.backup_restore import backup, restore


def seed(root):
    database, data = root / "database", root / "data"
    database.mkdir(parents=True)
    (data / "problems/demo/tests").mkdir(parents=True)
    (data / "problems/demo/assets").mkdir()
    (data / "avatars").mkdir()
    with sqlite3.connect(database / "oj.db") as db:
        db.execute("CREATE TABLE testcase (revision INTEGER, path TEXT)")
        db.execute("INSERT INTO testcase VALUES (7, 'problems/demo/tests/001.in')")
    (data / "problems/demo/tests/001.in").write_text("中 42\n")
    (data / "problems/demo/assets/image.png").write_bytes(b"image")
    (data / "avatars/avatar.png").write_bytes(b"avatar")
    return database, data


def test_offline_backup_restore_preserves_database_and_all_persistent_files(tmp_path):
    database, data = seed(tmp_path / "source")
    saved = backup(database, data, tmp_path / "snapshot", writers_stopped=True)
    restored = restore(saved, tmp_path / "restored")
    assert (restored / "data/problems/demo/tests/001.in").read_bytes() == (
        data / "problems/demo/tests/001.in"
    ).read_bytes()
    assert (restored / "data/avatars/avatar.png").read_bytes() == b"avatar"
    assert (restored / "data/problems/demo/assets/image.png").read_bytes() == b"image"
    with sqlite3.connect(restored / "database/oj.db") as db:
        assert db.execute("SELECT * FROM testcase").fetchone() == (
            7,
            "problems/demo/tests/001.in",
        )
    with pytest.raises(FileExistsError):
        restore(saved, restored)
    assert (restored / "data/avatars/avatar.png").read_bytes() == b"avatar"


def test_backup_requires_stopped_writers_and_nonoverlapping_new_destination(tmp_path):
    database, data = seed(tmp_path / "source")
    with pytest.raises(ValueError):
        backup(database, data, tmp_path / "bad")
    with pytest.raises(ValueError):
        backup(database, data, data / "snapshot", writers_stopped=True)
    assert not (tmp_path / "bad").exists()


def test_snapshot_corruption_and_symlinks_are_rejected_without_restoring(tmp_path):
    database, data = seed(tmp_path / "source")
    saved = backup(database, data, tmp_path / "snapshot", writers_stopped=True)
    (saved / "data/unexpected").write_text("unexpected")
    with pytest.raises(ValueError):
        restore(saved, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    (data / "escape").symlink_to(database / "oj.db")
    with pytest.raises(ValueError):
        backup(database, data, tmp_path / "bad-backup", writers_stopped=True)


def test_live_wal_contents_are_preserved_with_stopped_writer_connection(tmp_path):
    database, data = seed(tmp_path / "source")
    db = sqlite3.connect(database / "oj.db")
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO testcase VALUES (8, 'wal')")
        db.commit()  # No further writes: this models quiescent WAL files before checkpoint.
        saved = backup(database, data, tmp_path / "snapshot", writers_stopped=True)
        restored = restore(saved, tmp_path / "restored")
        with sqlite3.connect(restored / "database/oj.db") as loaded:
            assert loaded.execute(
                "SELECT revision FROM testcase ORDER BY revision"
            ).fetchall() == [(7,), (8,)]
    finally:
        db.close()
