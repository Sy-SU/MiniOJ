"""Bounded, reproducible C++ checker source bundles (never host executables)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from minioj.config import settings

VENDOR_DIR = Path(__file__).resolve().parents[1] / "vendor" / "testlib"
BUNDLE_LIMIT_BYTES = 4 * 1024 * 1024
FILE_LIMIT_BYTES = 1024 * 1024
MAX_FILES = 128
SCORING_CHECKERS = {"pointsinfo.cpp", "pointscmp.cpp"}
STANDARD_CHECKERS = (
    frozenset(path.name for path in (VENDOR_DIR / "checkers").glob("*.cpp"))
    - SCORING_CHECKERS
)


def safe_bundle_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1000:
        raise ValueError("Invalid checker source path.")
    path = PurePosixPath(value)
    if (
        value == "."
        or path.is_absolute()
        or str(path) != value
        or any(part in {"..", "main", "_minioj_case"} for part in path.parts)
        or any(char in value for char in "\\:\x00")
    ):
        raise ValueError("Unsafe checker source path.")
    return value


@dataclass(frozen=True)
class CheckerBundle:
    entrypoint: str
    files: dict[str, str]

    def validate(self) -> None:
        safe_bundle_path(self.entrypoint)
        if not isinstance(self.files, dict) or not 1 <= len(self.files) <= MAX_FILES:
            raise ValueError("Checker bundle contains too many or no files.")
        if self.entrypoint not in self.files:
            raise ValueError("Checker source is missing from its bundle.")
        total = 0
        for path, value in self.files.items():
            safe_bundle_path(path)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("Checker source files must be UTF-8 text without NUL.")
            size = len(value.encode("utf-8"))
            limit = (
                settings.source_limit_bytes
                if path == self.entrypoint
                else FILE_LIMIT_BYTES
            )
            if size > limit:
                raise ValueError("Checker source or resource exceeds its size limit.")
            total += size
        if total > BUNDLE_LIMIT_BYTES:
            raise ValueError("Checker source bundle exceeds 4 MiB.")
        paths = set(self.files)
        for path in paths:
            if any(str(parent) in paths for parent in PurePosixPath(path).parents):
                raise ValueError("Checker bundle has conflicting file/directory paths.")

    def serialize(self) -> str:
        self.validate()
        return json.dumps(
            {"entrypoint": self.entrypoint, "files": self.files},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def load(cls, encoded: str | None, checksum: str | None) -> CheckerBundle:
        if (
            not encoded
            or not checksum
            or len(encoded.encode("utf-8")) > BUNDLE_LIMIT_BYTES * 6
        ):
            raise ValueError("Checker bundle or checksum is missing or too large.")
        if hashlib.sha256(encoded.encode("utf-8")).hexdigest() != checksum:
            raise ValueError("Checker bundle checksum does not match.")
        try:
            value = json.loads(encoded)
            bundle = cls(value["entrypoint"], value["files"])
            bundle.validate()
            return bundle
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid checker bundle.") from exc
