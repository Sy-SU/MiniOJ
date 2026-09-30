from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.models import Problem, Sample, TestCase


def problem_test_dir(problem_id: str) -> Path:
    directory = (settings.problems_dir / problem_id / "tests").resolve()
    root = settings.problems_dir.resolve()
    if root not in directory.parents:
        raise ValueError("Unsafe problem id")
    return directory


def add_testcase(
    db: Session,
    problem: Problem,
    testcase_type: str,
    input_data: str,
    output_data: str,
) -> TestCase:
    if testcase_type not in {"sample", "hidden", "generated"}:
        raise ValueError("Invalid testcase type")
    max_order = (
        db.scalar(
            select(func.max(TestCase.order)).where(TestCase.problem_id == problem.id)
        )
        or 0
    )
    order = max_order + 1
    directory = problem_test_dir(problem.id)
    directory.mkdir(parents=True, exist_ok=True)
    input_path = directory / f"{order:03d}.in"
    output_path = directory / f"{order:03d}.out"
    input_path.write_text(input_data, encoding="utf-8")
    output_path.write_text(output_data, encoding="utf-8")
    testcase = TestCase(
        problem_id=problem.id,
        type=testcase_type,
        input_path=str(input_path.relative_to(settings.data_dir)),
        output_path=str(output_path.relative_to(settings.data_dir)),
        order=order,
    )
    db.add(testcase)
    if testcase_type == "sample":
        sample_order = (
            db.scalar(
                select(func.max(Sample.order)).where(Sample.problem_id == problem.id)
            )
            or 0
        )
        db.add(
            Sample(
                problem_id=problem.id,
                input=input_data,
                output=output_data,
                order=sample_order + 1,
            )
        )
    db.commit()
    db.refresh(testcase)
    return testcase


def testcase_contents(testcase: TestCase) -> tuple[str, str]:
    input_path = (settings.data_dir / testcase.input_path).resolve()
    output_path = (settings.data_dir / testcase.output_path).resolve()
    root = settings.data_dir.resolve()
    if root not in input_path.parents or root not in output_path.parents:
        raise ValueError("Unsafe testcase path")
    return input_path.read_text(encoding="utf-8"), output_path.read_text(
        encoding="utf-8"
    )


def remove_problem_files(problem_id: str) -> None:
    target = (settings.problems_dir / problem_id).resolve()
    root = settings.problems_dir.resolve()
    if root not in target.parents or not target.exists():
        return
    shutil.rmtree(target)
