from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

INDEX = r"[A-Z][0-9]{0,2}"
FLAT = re.compile(rf"(?:Codeforces[_-])?([1-9][0-9]*)[_-]?({INDEX})", re.IGNORECASE)
HELPERS = {"generator", "checker", "bad"}
REFERENCE_SELECTION_POLICY = "unmarked-fast-good-v1"


def reference_priority(path: Path) -> tuple[int, str]:
    # __Good is a stress-test companion, not evidence of an accepted solution.
    variant = path.stem.partition("__")[2].casefold()
    return ({"": 0, "fast": 1, "good": 2}[variant], str(path))


@dataclass(frozen=True, order=True)
class ProblemId:
    contest_id: int
    index: str

    @property
    def key(self) -> str:
        return f"codeforces:{self.contest_id}:{self.index}"

    @property
    def external_id(self) -> str:
        return f"{self.contest_id}{self.index}"

    @property
    def minioj_id(self) -> str:
        return "CF" + self.external_id

    @property
    def url(self) -> str:
        return (
            f"https://codeforces.com/problemset/problem/{self.contest_id}/{self.index}"
        )


def parse_problem(value: str) -> ProblemId:
    value = value.removeprefix("codeforces:").replace(":", "_")
    m = FLAT.fullmatch(value)
    if not m:
        raise ValueError(f"Unrecognized problem id: {value}")
    return ProblemId(int(m[1]), m[2].upper())


def scan(root: Path) -> tuple[dict[ProblemId, list[Path]], list[dict]]:
    if not root.is_dir():
        raise ValueError(f"Source directory does not exist: {root}")
    problems: dict[ProblemId, list[Path]] = {}
    records = []
    for path in sorted(root.rglob("*.cpp")):
        record = {"source_path": str(path)}
        stem, _, variant = path.stem.partition("__")
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            record.update(
                status="unresolved", reason="source symlink leaves scan boundary"
            )
        elif variant.lower() in HELPERS:
            record.update(status="ignored_helper", reason=variant)
        else:
            try:
                if re.fullmatch(INDEX, stem.upper()) and path.parent.name.isdecimal():
                    pid = ProblemId(int(path.parent.name), stem.upper())
                else:
                    pid = parse_problem(stem)
                    if (
                        path.parent.name.isdecimal()
                        and int(path.parent.name) != pid.contest_id
                    ):
                        raise ValueError("Filename and parent contest disagree")
                if variant and variant.lower() not in {"good", "fast"}:
                    raise ValueError("Unknown solution variant")
                if pid.contest_id <= 0:
                    raise ValueError("Invalid contest id")
                problems.setdefault(pid, []).append(path)
                record.update(
                    status="discovered",
                    contest_id=pid.contest_id,
                    problem_index=pid.index,
                    problem_id=pid.key,
                )
            except ValueError as exc:
                record.update(status="unresolved", reason=str(exc))
        records.append(record)
    for paths in problems.values():
        paths.sort(key=reference_priority)
    return dict(sorted(problems.items())), records
