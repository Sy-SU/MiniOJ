"""Validate LLM checkers in the same isolated runtime used by MiniOJ workers."""

from __future__ import annotations

from pathlib import Path

from minioj.config import settings
from minioj.judge.testlib import CheckerBundle

from .contracts import CheckerExamples
from .generation import CheckerError
from .storage import digest, read_json, write_json, write_text
from .validation import validate_input


def checker_bundle(directory: Path) -> CheckerBundle:
    return CheckerBundle(
        "checker.cpp", {"checker.cpp": (directory / "checker.cpp").read_text()}
    )


class CheckerHarness:
    def __init__(self, sandbox, directory: Path, spec):
        self.sandbox, self.directory, self.spec = sandbox, directory, spec
        self.records = []
        self.examples = CheckerExamples.model_validate(
            read_json(sandbox.directory / "checker_tests.json")
        )
        if spec.output_mode == "non_unique" and not self.examples.has_alternatives():
            raise CheckerError(
                "Non-unique checker tests require different valid answers for one input"
            )
        bundle = checker_bundle(sandbox.directory)
        bundle.validate()
        result = sandbox.judge.compile(
            directory, bundle.files[bundle.entrypoint], settings.checker_memory_mb
        )
        write_json(sandbox.directory / "logs/checker-compile.json", vars(result))
        if (
            result.exit_code != 0
            or result.timed_out
            or result.oom_killed
            or result.output_exceeded
            or result.stdout_truncated
            or result.stderr_truncated
        ):
            raise CheckerError("Checker compilation failed: " + result.stderr[:2000])

    def check(
        self,
        text: str,
        actual: str,
        expected: str,
        label: str,
        *,
        accept=True,
        error_type=CheckerError,
    ):
        case_dir = self.directory / "_minioj_case"
        case_dir.mkdir(exist_ok=True)
        for name, value in (("input", text), ("output", actual), ("answer", expected)):
            write_text(case_dir / name, value)
            (case_dir / name).chmod(0o644)
        result = self.sandbox.judge.execute(
            self.directory,
            "",
            settings.checker_time_limit_ms,
            settings.checker_memory_mb,
            arguments=[
                "/work/_minioj_case/" + f for f in ("input", "output", "answer")
            ],
            output_limit=4096,
        )
        write_json(self.sandbox.directory / "logs" / (label + ".json"), vars(result))
        if (
            result.timed_out
            or result.oom_killed
            or result.output_exceeded
            or result.exit_code not in {0, 1, 2, 4, 8}
            or result.stdout_truncated
            or result.stderr_truncated
            or not result.stdout_valid_utf8
        ):
            raise CheckerError(
                f"{label}: checker runtime error exit={result.exit_code}; "
                + result.stderr[:1000]
            )
        correct = (result.exit_code == 0) == accept
        record = {
            "label": label,
            "expected": "AC" if accept else "WA",
            "exit_code": result.exit_code,
            "passed": correct,
            "input_sha256": digest(text),
            "output_sha256": digest(actual),
        }
        self.records.append(record)
        if not correct:
            raise error_type(
                f"{label}: checker {'rejected a valid output' if accept else 'accepted an invalid output'}; "
                + result.stderr[:1000]
                + "\nInput:\n"
                + text[:1500]
                + "\nOutput:\n"
                + actual[:1500]
            )

    def selftest(self, validator: Path, reference: Path, statement):
        for number, case in enumerate(self.examples.cases, 1):
            label = f"checker-example-{number}"
            try:
                validate_input(case.input, self.spec)
                self.sandbox.run(validator, case.input, label + "-input")
            except ValueError as exc:
                raise CheckerError(
                    f"{label}: invalid checker test input: {exc}"
                ) from exc
            expected = case.valid_outputs[0]
            for i, output in enumerate(case.valid_outputs, 1):
                self.check(case.input, output, expected, f"{label}-valid-{i}")
            for i, wrong in enumerate(case.invalid_outputs, 1):
                self.check(
                    case.input,
                    wrong.output,
                    expected,
                    f"{label}-invalid-{i}",
                    accept=False,
                )
            # Generated examples alone cannot establish agreement with the local AC solution.
            output = self.sandbox.run(
                reference,
                case.input,
                label + "-reference-run",
                generation=False,
                time_limit_ms=statement.time_limit_ms,
                memory_mb=statement.memory_limit_mb,
            )
            self.check(case.input, output, expected, label + "-reference")
            self.check(case.input, "", expected, label + "-empty", accept=False)
            self.check(
                case.input,
                expected + "\n__unexpected_output_token__\n",
                expected,
                label + "-trailing",
                accept=False,
            )

    def save_report(self):
        report = {
            "bundle_sha256": digest(checker_bundle(self.sandbox.directory).serialize()),
            "source_sha256": digest(
                (self.sandbox.directory / "checker.cpp").read_bytes()
            ),
            "examples_sha256": digest(
                (self.sandbox.directory / "checker_tests.json").read_bytes()
            ),
            "rules": self.examples.rules,
            "checks": self.records,
        }
        write_json(self.sandbox.directory / "checker_validation.json", report)
        return report["bundle_sha256"]
