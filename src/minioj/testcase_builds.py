from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.judge import TestcaseBuildStatus
from minioj.models import Problem, TestcaseBuild, User
from minioj.problems import ensure_problem_mutable, mark_problem_changed


def _decode_utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8 text.") from exc


def source_text(data: bytes, label: str) -> str:
    if len(data) > settings.source_limit_bytes:
        raise ValueError(
            f"{label} must not exceed {settings.source_limit_bytes} bytes."
        )
    source = _decode_utf8(data, label)
    if not source.strip():
        raise ValueError(f"{label} must not be empty.")
    return source


def input_text(data: bytes) -> str:
    if len(data) > settings.testcase_file_limit_bytes:
        raise ValueError(
            "Testcase input must not exceed "
            f"{settings.testcase_file_limit_bytes} bytes."
        )
    return _decode_utf8(data, "Testcase input")


def save_standard_solution(db: Session, problem: Problem, source_data: bytes) -> str:
    ensure_problem_mutable(db, problem.id)
    source = source_text(source_data, "Standard solution")
    digest = hashlib.sha256(source_data).hexdigest()
    problem.standard_source = source
    problem.standard_sha256 = digest
    problem.standard_updated_at = datetime.now(UTC)
    mark_problem_changed(problem)
    try:
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return digest


def _new_build(
    problem: Problem,
    admin: User,
    *,
    kind: str,
    testcase_type: str,
    input_data: str | None = None,
    generator_source: str | None = None,
    case_count: int = 1,
    base_seed: int = 1,
) -> TestcaseBuild:
    if not problem.standard_source or not problem.standard_sha256:
        raise ValueError("Save a C++20 standard solution before building testcases.")
    return TestcaseBuild(
        problem_id=problem.id,
        created_by=admin.id,
        kind=kind,
        testcase_type=testcase_type,
        standard_source=problem.standard_source,
        standard_sha256=problem.standard_sha256,
        input_data=input_data,
        generator_source=generator_source,
        case_count=case_count,
        base_seed=base_seed,
        status=TestcaseBuildStatus.QUEUED.value,
    )


def queue_input_build(
    db: Session,
    problem: Problem,
    admin: User,
    source_data: bytes,
    testcase_type: str,
) -> TestcaseBuild:
    ensure_problem_mutable(db, problem.id)
    if testcase_type not in {"sample", "hidden"}:
        raise ValueError("Uploaded inputs must be sample or hidden testcases.")
    build = _new_build(
        problem,
        admin,
        kind="input",
        testcase_type=testcase_type,
        input_data=input_text(source_data),
    )
    db.add(build)
    try:
        db.commit()
        db.refresh(build)
    except BaseException:
        db.rollback()
        raise
    return build


def queue_generator_build(
    db: Session,
    problem: Problem,
    admin: User,
    generator_data: bytes,
    case_count: int,
    base_seed: int,
) -> TestcaseBuild:
    ensure_problem_mutable(db, problem.id)
    if not 1 <= case_count <= settings.generator_max_cases:
        raise ValueError(
            f"Generator case count must be between 1 and {settings.generator_max_cases}."
        )
    if not 0 <= base_seed <= 2_147_483_647:
        raise ValueError("Generator base seed must be between 0 and 2147483647.")
    if base_seed + case_count - 1 > 2_147_483_647:
        raise ValueError("Generator seeds must not exceed 2147483647.")
    generator_source = source_text(generator_data, "C++20 generator")
    build = _new_build(
        problem,
        admin,
        kind="generator",
        testcase_type="generated",
        generator_source=generator_source,
        case_count=case_count,
        base_seed=base_seed,
    )
    db.add(build)
    try:
        db.commit()
        db.refresh(build)
    except BaseException:
        db.rollback()
        raise
    return build
