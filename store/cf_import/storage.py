from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat()


def digest(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".cf-import-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def importer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "Another importer is running for this state file"
            ) from exc
        yield


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data = read_json(path) if path.exists() else {"version": 1, "problems": {}}
        if self.data.get("version") != 1:
            raise ValueError("Unsupported importer state version")

    def entry(self, key: str) -> dict:
        return self.data["problems"].setdefault(key, {"status": "discovered"})

    def update(self, key: str, status: str | None = None, **values) -> dict:
        entry = self.entry(key)
        entry.update(values)
        if status:
            entry["status"] = status
            if status not in {"failed", "minioj_validation_failed"}:
                entry["last_completed_step"] = status
        entry["updated_at"] = now()
        write_json(self.path, self.data)
        return entry
