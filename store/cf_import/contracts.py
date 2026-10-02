from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Sample(Document):
    input: str = Field(min_length=1)
    output: str = Field(min_length=1)


class Statement(Document):
    problem_id: str
    title: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    input_format: str = Field(min_length=1)
    output_format: str = Field(min_length=1)
    constraints: str = Field(min_length=1)
    notes: str = ""
    samples: list[Sample] = Field(min_length=1)
    source_url: str
    time_limit_ms: int = Field(ge=100, le=30000)
    memory_limit_mb: int = Field(ge=16, le=2048)


class InputNode(Document):
    """Small token grammar; arithmetic expressions can use previously read ints."""

    kind: Literal["int", "string", "array", "repeat", "graph"]
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    minimum: int | str | None = None
    maximum: int | str | None = None
    length: int | str | None = None
    alphabet: str | None = None
    distinct: bool = False
    permutation: bool = False
    sorted: bool = False
    vertices: int | str | None = None
    tree: bool = False
    simple: bool = True
    connected: bool = False
    directed: bool = False
    dag: bool = False
    children: list[InputNode] = Field(default_factory=list)

    @model_validator(mode="after")
    def required_fields(self):
        if self.kind == "int" and (self.minimum is None or self.maximum is None):
            raise ValueError("int nodes require minimum and maximum")
        if self.kind in {"array", "repeat", "graph"} and self.length is None:
            raise ValueError("array/repeat/graph nodes require length")
        if self.kind == "array" and (
            len(self.children) != 1 or self.children[0].kind not in {"int", "string"}
        ):
            raise ValueError("array requires one scalar child")
        if self.kind == "repeat" and not self.children:
            raise ValueError("repeat requires children")
        if self.kind == "graph" and self.vertices is None:
            raise ValueError(
                "graph requires vertices; graph tokens are unweighted u v pairs"
            )
        if self.dag and not self.directed:
            raise ValueError("DAG must be directed")
        return self


class TotalConstraint(Document):
    field: str
    maximum: int = Field(ge=0)


class ProblemSpec(Document):
    input_structure: str = Field(min_length=1)
    has_test_cases: bool
    constraints: list[str] = Field(min_length=1)
    semantic_constraints: list[str]
    special_properties: list[str]
    generator_notes: list[str]
    output_mode: Literal[
        "tokens", "yesno", "lines", "non_unique", "floating", "interactive"
    ]
    input_schema: list[InputNode]
    total_constraints: list[TotalConstraint]
    validation_warnings: list[str]

    @model_validator(mode="after")
    def partial_validation(self):
        if not self.input_schema and not self.validation_warnings:
            raise ValueError(
                "An empty input_schema requires explicit validation_warnings"
            )
        return self


CATEGORIES = {"small", "boundary", "random", "structured", "large", "maximum"}


class PlannedTest(Document):
    id: int = Field(ge=1, le=50)
    category: Literal[
        "small", "boundary", "random", "structured", "adversarial", "large", "maximum"
    ]
    description: str = Field(min_length=1)


class GeneratorPlan(Document):
    boundary_cases: list[str] = Field(min_length=1)
    small_cases: list[str] = Field(min_length=1)
    random_cases: list[str] = Field(min_length=1)
    structured_cases: list[str] = Field(min_length=1)
    adversarial_cases: list[str] = Field(min_length=1)
    maximum_size_cases: list[str] = Field(min_length=1)
    tests: list[PlannedTest] = Field(min_length=6, max_length=50)

    @model_validator(mode="after")
    def coverage(self):
        if [t.id for t in self.tests] != list(range(1, len(self.tests) + 1)):
            raise ValueError("Test ids must be consecutive starting at 1")
        if not CATEGORIES.issubset({t.category for t in self.tests}):
            raise ValueError("Plan must cover all six required categories")
        return self


class GeneratedCode(Document):
    generator_cpp: str = Field(min_length=20, max_length=262144)
    validator_cpp: str = Field(min_length=20, max_length=262144)


class WrongAnswer(Document):
    output: str = Field(max_length=65536)
    reason: str = Field(min_length=1, max_length=1000)


class CheckerCase(Document):
    description: str = Field(min_length=1, max_length=1000)
    input: str = Field(min_length=1, max_length=65536)
    valid_outputs: list[str] = Field(min_length=1, max_length=4)
    invalid_outputs: list[WrongAnswer] = Field(min_length=2, max_length=6)

    @model_validator(mode="after")
    def bounded_outputs(self):
        if any(not s.strip() or len(s.encode()) > 65536 for s in self.valid_outputs):
            raise ValueError("Valid checker examples must be nonempty bounded UTF-8")
        return self


class CheckerExamples(Document):
    rules: list[str] = Field(min_length=1, max_length=30)
    cases: list[CheckerCase] = Field(min_length=3, max_length=12)

    def has_alternatives(self) -> bool:
        return any(
            len({tuple(s.split()) for s in case.valid_outputs}) >= 2
            for case in self.cases
        )


class NonUniqueCheckerExamples(CheckerExamples):
    @model_validator(mode="after")
    def alternative_answers(self):
        if not self.has_alternatives():
            raise ValueError(
                "Non-unique checker tests require different valid answers for one input"
            )
        return self


class GeneratedChecker(Document):
    checker_cpp: str = Field(min_length=20, max_length=262144)
