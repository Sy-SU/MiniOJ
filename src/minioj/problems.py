from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.models import Problem, Sample, Submission, TestCase, TestcaseBuild, utcnow

logger = logging.getLogger("minioj.problems")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
TESTCASE_TYPES = frozenset({"sample", "hidden", "generated"})


def ensure_problem_mutable(db: Session, problem_id: str) -> None:
    # A no-op write serializes SQLite readers of testcase files with mutations.
    # The lock lasts until commit/rollback; no Docker work happens while held.
    result = db.execute(
        update(Problem)
        .where(Problem.id == problem_id, Problem.deleted_at.is_(None))
        .values(updated_at=Problem.updated_at)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("This problem has been deleted or no longer exists.")
    problem = db.get(Problem, problem_id)
    db.refresh(problem)


def mark_problem_changed(problem: Problem) -> None:
    problem.revision += 1


def update_problem(db: Session, problem: Problem, values: dict) -> None:
    ensure_problem_mutable(db, problem.id)
    changed = False
    for key, value in values.items():
        if key != "id" and getattr(problem, key) != value:
            setattr(problem, key, value)
            changed = True
    if changed:
        mark_problem_changed(problem)
    try:
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _problem_directory(problem_id: str) -> Path:
    if (
        not problem_id
        or problem_id in {".", ".."}
        or Path(problem_id).name != problem_id
    ):
        raise ValueError("Unsafe problem id")
    root = settings.problems_dir.resolve()
    problem_directory = settings.problems_dir / problem_id
    if problem_directory.is_symlink():
        raise ValueError("Unsafe problem directory")
    resolved = problem_directory.resolve()
    if resolved.parent != root:
        raise ValueError("Unsafe problem id")
    return resolved


def problem_test_dir(problem_id: str) -> Path:
    directory = _problem_directory(problem_id) / "tests"
    if directory.is_symlink():
        raise ValueError("Unsafe testcase directory")
    return directory.resolve()


def _testcase_path(testcase: TestCase, stored_path: str) -> Path:
    relative = Path(stored_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Unsafe testcase path")
    path = settings.data_dir / relative
    if path.is_symlink():
        raise ValueError("Unsafe testcase symlink")
    resolved = path.resolve()
    if resolved.parent != problem_test_dir(testcase.problem_id):
        raise ValueError("Unsafe testcase path")
    return resolved


def _checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_checksum(value: str | None, label: str) -> str | None:
    expected = value.strip().lower() if value else None
    if expected and not SHA256_RE.fullmatch(expected):
        raise ValueError(f"{label} SHA-256 must contain 64 hexadecimal characters.")
    return expected


def _testcase_data(value: str | bytes, label: str) -> bytes:
    data = value.encode("utf-8") if isinstance(value, str) else value
    if len(data) > settings.testcase_file_limit_bytes:
        raise ValueError(
            f"{label} must not exceed {settings.testcase_file_limit_bytes} bytes."
        )
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8 text.") from exc
    return data


def _validate_payload(
    value: str | bytes,
    label: str,
    expected_sha256: str | None,
) -> tuple[bytes, str]:
    data = _testcase_data(value, label)
    digest = _checksum(data)
    expected = _expected_checksum(expected_sha256, label)
    if expected and expected != digest:
        raise ValueError(f"{label} SHA-256 does not match the uploaded content.")
    return data, digest


def _write_payload(directory: Path, order: int, suffix: str, data: bytes) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or directory.resolve() != directory:
        raise ValueError("Unsafe testcase directory")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".upload-", suffix=suffix, dir=directory
    )
    temporary = Path(temporary_name)
    target = directory / f"{order:03d}-{uuid.uuid4().hex}{suffix}"
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return target


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(settings.data_dir.resolve()))
    except ValueError as exc:
        raise ValueError("Unsafe testcase path") from exc


def testcase_bytes(testcase: TestCase) -> tuple[bytes, bytes]:
    input_data = _testcase_path(testcase, testcase.input_path).read_bytes()
    output_data = _testcase_path(testcase, testcase.output_path).read_bytes()
    if testcase.input_sha256 and _checksum(input_data) != testcase.input_sha256:
        raise ValueError("Testcase input checksum mismatch")
    if testcase.output_sha256 and _checksum(output_data) != testcase.output_sha256:
        raise ValueError("Testcase output checksum mismatch")
    return input_data, output_data


def testcase_contents(testcase: TestCase) -> tuple[str, str]:
    input_data, output_data = testcase_bytes(testcase)
    try:
        return input_data.decode("utf-8"), output_data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Testcase files must contain valid UTF-8 text") from exc


def _next_sample_order(db: Session, problem_id: str) -> int:
    return (
        db.scalar(select(func.max(Sample.order)).where(Sample.problem_id == problem_id))
        or 0
    ) + 1


def _sample_for_testcase(db: Session, testcase: TestCase) -> Sample | None:
    sample = db.scalar(select(Sample).where(Sample.testcase_id == testcase.id))
    if sample is not None or testcase.type != "sample":
        return sample
    sample_position = db.scalar(
        select(func.count(TestCase.id)).where(
            TestCase.problem_id == testcase.problem_id,
            TestCase.type == "sample",
            TestCase.order <= testcase.order,
        )
    )
    sample = db.scalar(
        select(Sample).where(
            Sample.problem_id == testcase.problem_id,
            Sample.order == sample_position,
            Sample.testcase_id.is_(None),
        )
    )
    if sample is not None:
        sample.testcase_id = testcase.id
    return sample


def _remove_paths(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove obsolete testcase file %s", path)


def add_testcase(
    db: Session,
    problem: Problem,
    testcase_type: str,
    input_data: str | bytes,
    output_data: str | bytes,
    *,
    input_sha256: str | None = None,
    output_sha256: str | None = None,
) -> TestCase:
    if testcase_type not in TESTCASE_TYPES:
        raise ValueError("Invalid testcase type")
    ensure_problem_mutable(db, problem.id)
    input_payload, input_digest = _validate_payload(
        input_data, "Testcase input", input_sha256
    )
    output_payload, output_digest = _validate_payload(
        output_data, "Expected output", output_sha256
    )
    max_order = (
        db.scalar(
            select(func.max(TestCase.order)).where(TestCase.problem_id == problem.id)
        )
        or 0
    )
    order = max_order + 1
    directory = problem_test_dir(problem.id)
    created_paths: list[Path] = []
    try:
        input_path = _write_payload(directory, order, ".in", input_payload)
        created_paths.append(input_path)
        output_path = _write_payload(directory, order, ".out", output_payload)
        created_paths.append(output_path)
        testcase = TestCase(
            problem_id=problem.id,
            type=testcase_type,
            input_path=_relative(input_path),
            output_path=_relative(output_path),
            input_sha256=input_digest,
            output_sha256=output_digest,
            order=order,
        )
        db.add(testcase)
        db.flush()
        if testcase_type == "sample":
            db.add(
                Sample(
                    problem_id=problem.id,
                    testcase_id=testcase.id,
                    input=input_payload.decode("utf-8"),
                    output=output_payload.decode("utf-8"),
                    order=_next_sample_order(db, problem.id),
                )
            )
        mark_problem_changed(problem)
        db.commit()
        db.refresh(testcase)
        return testcase
    except IntegrityError as exc:
        db.rollback()
        _remove_paths(created_paths)
        raise ValueError("Concurrent testcase change; retry the operation.") from exc
    except BaseException:
        db.rollback()
        _remove_paths(created_paths)
        raise


def add_testcase_batch(
    db: Session,
    problem: Problem,
    testcase_type: str,
    cases: list[tuple[str | bytes, str | bytes]],
    *,
    finalize: Callable[[list[TestCase]], None] | None = None,
) -> list[TestCase]:
    if testcase_type not in TESTCASE_TYPES:
        raise ValueError("Invalid testcase type")
    if not cases:
        raise ValueError("No testcase data was generated")
    ensure_problem_mutable(db, problem.id)
    validated = [
        (
            *_validate_payload(input_data, "Testcase input", None),
            *_validate_payload(output_data, "Expected output", None),
        )
        for input_data, output_data in cases
    ]
    max_order = (
        db.scalar(
            select(func.max(TestCase.order)).where(TestCase.problem_id == problem.id)
        )
        or 0
    )
    sample_order = _next_sample_order(db, problem.id)
    directory = problem_test_dir(problem.id)
    created_paths: list[Path] = []
    testcases: list[TestCase] = []
    try:
        for offset, (
            input_payload,
            input_digest,
            output_payload,
            output_digest,
        ) in enumerate(validated, start=1):
            order = max_order + offset
            input_path = _write_payload(directory, order, ".in", input_payload)
            created_paths.append(input_path)
            output_path = _write_payload(directory, order, ".out", output_payload)
            created_paths.append(output_path)
            testcase = TestCase(
                problem_id=problem.id,
                type=testcase_type,
                input_path=_relative(input_path),
                output_path=_relative(output_path),
                input_sha256=input_digest,
                output_sha256=output_digest,
                order=order,
            )
            db.add(testcase)
            db.flush()
            testcases.append(testcase)
            if testcase_type == "sample":
                db.add(
                    Sample(
                        problem_id=problem.id,
                        testcase_id=testcase.id,
                        input=input_payload.decode("utf-8"),
                        output=output_payload.decode("utf-8"),
                        order=sample_order,
                    )
                )
                sample_order += 1
        if finalize is not None:
            finalize(testcases)
        mark_problem_changed(problem)
        db.commit()
        return testcases
    except IntegrityError as exc:
        db.rollback()
        _remove_paths(created_paths)
        raise ValueError("Concurrent testcase change; retry the operation.") from exc
    except BaseException:
        db.rollback()
        _remove_paths(created_paths)
        raise


def update_testcase(
    db: Session,
    problem: Problem,
    testcase_id: int,
    testcase_type: str,
    *,
    input_data: str | bytes | None = None,
    output_data: str | bytes | None = None,
    input_sha256: str | None = None,
    output_sha256: str | None = None,
) -> TestCase:
    if testcase_type not in TESTCASE_TYPES:
        raise ValueError("Invalid testcase type")
    ensure_problem_mutable(db, problem.id)
    testcase = db.scalar(
        select(TestCase).where(
            TestCase.id == testcase_id, TestCase.problem_id == problem.id
        )
    )
    if testcase is None:
        raise LookupError("Testcase not found")
    old_input_path = _testcase_path(testcase, testcase.input_path)
    old_output_path = _testcase_path(testcase, testcase.output_path)
    current_input, current_output = testcase_bytes(testcase)
    sample = _sample_for_testcase(db, testcase)
    old_type = testcase.type
    created_paths: list[Path] = []
    obsolete_paths: list[Path] = []
    try:
        if input_data is not None:
            payload, digest = _validate_payload(
                input_data, "Testcase input", input_sha256
            )
            path = _write_payload(
                problem_test_dir(problem.id), testcase.order, ".in", payload
            )
            created_paths.append(path)
            obsolete_paths.append(old_input_path)
            testcase.input_path = _relative(path)
            testcase.input_sha256 = digest
        else:
            expected = _expected_checksum(input_sha256, "Testcase input")
            digest = _checksum(current_input)
            if expected and expected != digest:
                raise ValueError(
                    "Testcase input SHA-256 does not match the stored content."
                )
            testcase.input_sha256 = digest
        if output_data is not None:
            payload, digest = _validate_payload(
                output_data, "Expected output", output_sha256
            )
            path = _write_payload(
                problem_test_dir(problem.id), testcase.order, ".out", payload
            )
            created_paths.append(path)
            obsolete_paths.append(old_output_path)
            testcase.output_path = _relative(path)
            testcase.output_sha256 = digest
        else:
            expected = _expected_checksum(output_sha256, "Expected output")
            digest = _checksum(current_output)
            if expected and expected != digest:
                raise ValueError(
                    "Expected output SHA-256 does not match the stored content."
                )
            testcase.output_sha256 = digest
        testcase.type = testcase_type
        db.flush()
        final_input, final_output = testcase_contents(testcase)
        if old_type == "sample" and testcase_type != "sample":
            if sample is not None:
                db.delete(sample)
        elif testcase_type == "sample":
            if sample is None:
                sample = Sample(
                    problem_id=problem.id,
                    testcase_id=testcase.id,
                    order=_next_sample_order(db, problem.id),
                )
                db.add(sample)
            sample.input = final_input
            sample.output = final_output
        mark_problem_changed(problem)
        db.commit()
        db.refresh(testcase)
    except BaseException:
        db.rollback()
        _remove_paths(created_paths)
        raise
    _remove_paths(obsolete_paths)
    return testcase


def delete_testcase(db: Session, problem: Problem, testcase_id: int) -> TestCase:
    ensure_problem_mutable(db, problem.id)
    testcase = db.scalar(
        select(TestCase).where(
            TestCase.id == testcase_id, TestCase.problem_id == problem.id
        )
    )
    if testcase is None:
        raise LookupError("Testcase not found")
    paths = [
        _testcase_path(testcase, testcase.input_path),
        _testcase_path(testcase, testcase.output_path),
    ]
    sample = _sample_for_testcase(db, testcase)
    try:
        if sample is not None:
            db.delete(sample)
        db.delete(testcase)
        mark_problem_changed(problem)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    _remove_paths(paths)
    return testcase


def delete_problem(db: Session, problem: Problem) -> None:
    ensure_problem_mutable(db, problem.id)
    try:
        problem.deleted_at = utcnow()
        # Retain the row and files so historical references and running judges
        # remain valid. A deleted ID cannot silently become a different problem.
        db.execute(
            update(Submission)
            .where(Submission.problem_id == problem.id, Submission.status == "QUEUED")
            .values(
                status="FINISHED",
                verdict="IE",
                finished_at=utcnow(),
                judge_result=json.dumps(
                    {
                        "verdict": "IE",
                        "summary": "This problem has been deleted before judging started.",
                        "tests": {"total": 0, "passed": 0, "failed_test": None},
                        "resources": {"time_ms": 0, "memory_kb": 0},
                    }
                ),
            )
        )
        db.execute(
            update(TestcaseBuild)
            .where(
                TestcaseBuild.problem_id == problem.id,
                TestcaseBuild.status.in_(["QUEUED", "RUNNING"]),
            )
            .values(
                status="FAILED",
                error="This problem has been deleted; testcase build cancelled.",
                finished_at=utcnow(),
            )
        )
        db.commit()
    except BaseException:
        db.rollback()
        raise
