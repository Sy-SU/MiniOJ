"""Synthetic upstreams for importer tests; these are NOT Codeforces AC evidence."""

from __future__ import annotations

from pathlib import Path

from store.cf_import.contracts import (
    CheckerExamples,
    GeneratedChecker,
    GeneratedCode,
    GeneratorPlan,
    ProblemSpec,
)
from store.cf_import.scanner import ProblemId

FIXTURES = Path(__file__).resolve().parents[1] / "tests/fixtures/cf_import"
PID = ProblemId(99999, "A")
SPECIAL_PID = ProblemId(99999, "B")
METADATA = {
    "contestId": PID.contest_id,
    "index": PID.index,
    "name": "Fixture Array Sum",
    "rating": 800,
    "tags": ["implementation"],
}
SPEC = {
    "input_structure": "t cases, each n then an array of n integers",
    "has_test_cases": True,
    "constraints": ["1<=t<=5", "1<=n<=1000", "-100<=a<=100", "sum n<=2000"],
    "semantic_constraints": [],
    "special_properties": [],
    "generator_notes": ["negative, zero, maximum-length arrays"],
    "output_mode": "tokens",
    "validation_warnings": [],
    "input_schema": [
        {"kind": "int", "name": "t", "minimum": 1, "maximum": 5},
        {
            "kind": "repeat",
            "name": "cases",
            "length": "t",
            "children": [
                {"kind": "int", "name": "n", "minimum": 1, "maximum": 1000},
                {
                    "kind": "array",
                    "name": "a",
                    "length": "n",
                    "children": [
                        {
                            "kind": "int",
                            "name": "value",
                            "minimum": -100,
                            "maximum": 100,
                        }
                    ],
                },
            ],
        },
    ],
    "total_constraints": [{"field": "n", "maximum": 2000}],
}
PLAN = {
    "boundary_cases": ["min/max values"],
    "small_cases": ["n=1,2"],
    "random_cases": ["seeded random arrays"],
    "structured_cases": ["zero/alternating arrays"],
    "adversarial_cases": ["cancellation"],
    "maximum_size_cases": ["n=1000"],
    "tests": [
        {"id": i + 1, "category": c, "description": f"Fixture pattern {i + 1}"}
        for i, c in enumerate(
            [
                "small",
                "boundary",
                "random",
                "structured",
                "boundary",
                "structured",
                "random",
                "small",
                "large",
                "large",
                "maximum",
                "maximum",
            ]
        )
    ],
}

CHECKER_EXAMPLES = {
    "rules": [
        "Exactly two integers per case",
        "Each integer is within +/- 10^12",
        "Their sum equals the input array sum",
        "No excess tokens",
    ],
    "cases": [
        {
            "description": "Positive sum with alternatives",
            "input": "1\n3\n1 2 3\n",
            "valid_outputs": ["6 0\n", "0 6\n", "8 -2\n"],
            "invalid_outputs": [
                {"output": "6\n", "reason": "Missing second integer"},
                {"output": "7 0\n", "reason": "Wrong sum"},
            ],
        },
        {
            "description": "Negative sum and bounds",
            "input": "1\n1\n-100\n",
            "valid_outputs": ["-100 0\n", "0 -100\n"],
            "invalid_outputs": [
                {"output": "100 0\n", "reason": "Wrong sum"},
                {
                    "output": "1000000000001 -1000000000101\n",
                    "reason": "Out of bounds even though sum is right",
                },
            ],
        },
        {
            "description": "Multiple cases and EOF",
            "input": "2\n1\n0\n2\n100 -100\n",
            "valid_outputs": ["0 0\n1 -1\n"],
            "invalid_outputs": [
                {"output": "0 0\n", "reason": "Missing case"},
                {"output": "0 0\n1 -1\n42\n", "reason": "Extra token"},
                {"output": "0 0\n1x -1\n", "reason": "Malformed integer"},
            ],
        },
    ],
}


def constructive_reference() -> str:
    return (
        (FIXTURES / "reference.cpp")
        .read_text()
        .replace("std::cout << sum <<", 'std::cout << 0 << " " << sum <<')
    )


class UpstreamHTTP:
    def __init__(self, *, constructive=False):
        self.calls = []
        self.constructive = constructive
        self.pid = SPECIAL_PID if constructive else PID
        self.metadata = dict(METADATA, index=self.pid.index)
        if constructive:
            self.metadata["name"] = "Fixture Array Decomposition"

    def request(self, method, url, *, params=None, payload=None, token="", text=False):
        self.calls.append((method, url))
        if url.endswith("/user.status"):
            return {
                "status": "OK",
                "result": [
                    {
                        "id": 42,
                        "verdict": "OK",
                        "problem": self.metadata,
                        "author": {"members": [{"handle": "FixtureUser"}]},
                    }
                ],
            }
        if url.endswith("/problemset.problems"):
            return {"status": "OK", "result": {"problems": [self.metadata]}}
        if text and url == self.pid.url:
            html = (FIXTURES / "statement.html").read_text()
            if self.constructive:
                html = html.replace(
                    "A. Fixture Array Sum", "B. Fixture Array Decomposition"
                )
                html = html.replace(
                    "print the sum of its elements.",
                    "print any two integers x, y whose sum equals the sum of its elements.",
                )
                html = html.replace(
                    "Print one integer per case.",
                    "Print two integers per case, each within [-10^12, 10^12]. Any valid pair is accepted.",
                )
                html = html.replace("6<br>-100<br>", "6 0<br>-100 0<br>")
                html = html.replace(
                    "The answer is unique.", "Multiple answers are valid."
                )
            return html
        schema_text = (
            payload["messages"][1]["content"]
            if "messages" in payload
            else payload["input"][1]["content"]
        )
        if "Stage A:" in schema_text:
            spec = dict(SPEC)
            if self.constructive:
                spec.update(
                    output_mode="non_unique",
                    special_properties=["Any bounded integer decomposition is valid"],
                )
            value = ProblemSpec.model_validate(spec).model_dump_json()
        elif "Stage B:" in schema_text:
            value = GeneratorPlan.model_validate(PLAN).model_dump_json()
        elif "Stage D examples:" in schema_text:
            value = CheckerExamples.model_validate(CHECKER_EXAMPLES).model_dump_json()
        elif "Stage D checker:" in schema_text:
            value = GeneratedChecker(
                checker_cpp=(FIXTURES / "checker.cpp").read_text()
            ).model_dump_json()
        else:
            value = GeneratedCode(
                generator_cpp=(FIXTURES / "gen.cpp").read_text(),
                validator_cpp=(FIXTURES / "validator.cpp").read_text(),
            ).model_dump_json()
        if url.endswith("/responses"):
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": value}],
                    }
                ],
            }
        return {"choices": [{"finish_reason": "stop", "message": {"content": value}}]}
