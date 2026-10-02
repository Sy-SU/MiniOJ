"""Offline SQLite + data snapshot; never stop services or overwrite a destination."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory(root):
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Snapshot directories must not contain symlinks")
        if path.is_file():
            files[path.relative_to(root).as_posix()] = file_hash(path)
        elif not path.is_dir():
            raise ValueError(
                "Snapshot directories must contain only regular files/directories"
            )
    return files


def check_sqlite(path):
    if not path.is_file():
        raise ValueError("SQLite file is missing")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("SQLite integrity check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign key check failed")


def fresh_destination(destination, sources):
    target = Path(destination).absolute()
    for source in sources:
        source = Path(source).resolve()
        if target == source or source in target.parents or target in source.parents:
            raise ValueError("Source and destination directories must not overlap")
    # Refuse symlinked parents and every pre-existing target, even empty ones.
    if any(p.is_symlink() for p in [target, *target.parents]):
        raise ValueError("Destination cannot use symlinked paths")
    target.mkdir(parents=True, mode=0o700, exist_ok=False)
    return target


def backup(
    database_dir, data_dir, destination, *, writers_stopped=False, database_name="oj.db"
):
    if not writers_stopped:
        raise ValueError(
            "Stop all Web/Worker/other writers before taking this offline snapshot"
        )
    if Path(database_name).name != database_name or database_name in {"", ".", ".."}:
        raise ValueError("Database name must be a filename")
    database_dir, data_dir = Path(database_dir).absolute(), Path(data_dir).absolute()
    if any(
        p.is_symlink()
        for source in (database_dir, data_dir)
        for p in [source, *source.parents]
    ):
        raise ValueError("Source directories cannot use symlinked paths")
    if not database_dir.is_dir() or not data_dir.is_dir():
        raise ValueError("Both persistent source directories must exist")
    check_sqlite(database_dir / database_name)
    database_files, data_files = inventory(database_dir), inventory(data_dir)
    target = fresh_destination(destination, [database_dir, data_dir])
    shutil.copytree(
        database_dir, target / "database"
    )  # Includes WAL/SHM/journal, not just .db.
    shutil.copytree(
        data_dir, target / "data"
    )  # Includes tests, statement assets and avatars.
    if inventory(database_dir) != database_files or inventory(data_dir) != data_files:
        raise ValueError(
            "Source changed during offline backup; discard this incomplete snapshot"
        )
    manifest = {
        "format": 1,
        "database_name": database_name,
        "files": {
            **{"database/" + k: v for k, v in database_files.items()},
            **{"data/" + k: v for k, v in data_files.items()},
        },
    }
    if inventory(target) != manifest["files"]:
        raise ValueError("Snapshot hashes differ from source")
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return target


def restore(snapshot, destination):
    snapshot = Path(snapshot).absolute()
    if snapshot.is_symlink() or (snapshot / "manifest.json").is_symlink():
        raise ValueError("Snapshot cannot use symlinks")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("Unsupported snapshot manifest")
    name = manifest.get("database_name")
    if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
        raise ValueError("Invalid snapshot database filename")
    files = inventory(snapshot)
    files.pop("manifest.json", None)
    if files != manifest["files"]:
        raise ValueError("Snapshot checksum mismatch")
    target = fresh_destination(destination, [snapshot])
    shutil.copytree(snapshot / "database", target / "database")
    shutil.copytree(snapshot / "data", target / "data")
    if inventory(target) != manifest["files"]:
        raise ValueError("Restored file checksum mismatch")
    check_sqlite(target / "database" / name)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    save = actions.add_parser("backup")
    save.add_argument("--database-dir", required=True)
    save.add_argument("--database-name", default="oj.db")
    save.add_argument("--data-dir", required=True)
    save.add_argument("--destination", required=True)
    save.add_argument("--writers-stopped", action="store_true", required=True)
    load = actions.add_parser("restore")
    load.add_argument("--snapshot", required=True)
    load.add_argument("--destination", required=True)
    args = parser.parse_args()
    try:
        if args.action == "backup":
            target = backup(
                args.database_dir,
                args.data_dir,
                args.destination,
                writers_stopped=args.writers_stopped,
                database_name=args.database_name,
            )
        else:
            target = restore(args.snapshot, args.destination)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(
            1,
            f"Offline snapshot failed ({type(exc).__name__}); destination is not overwritten or deleted.\n",
        )
    print(f"{args.action} completed: {target}")


if __name__ == "__main__":
    main()
