from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .config import Config
from .contracts import (
    CheckerExamples,
    GeneratedChecker,
    GeneratedCode,
    GeneratorPlan,
    NonUniqueCheckerExamples,
    ProblemSpec,
    Statement,
)
from .llm import LLMClient
from .storage import digest, read_json, write_json, write_text
from .validation import validate_input


def stage_document(path: Path, model, llm: LLMClient, stage: str, prompt: str):
    if path.exists():
        return model.model_validate(read_json(path))
    value = llm.generate(stage, prompt, model, path.parent)
    write_json(path, value.model_dump())
    return value


def prepare_spec(
    directory: Path, statement: Statement, metadata: dict, llm: LLMClient
) -> ProblemSpec:
    prompt = (
        "Stage A: derive a Problem Spec from ALL the statement, semantics and input constraints. "
        "Identify multiple cases, global sum bounds, permutation, distinct/sorted arrays, "
        "binary strings, index/value ranges, trees, connected/simple graphs and DAGs. "
        "Classify output_mode honestly: non_unique for constructive/multiple valid answers, "
        "floating for tolerance checking, interactive for interaction. Ordinary unique answers "
        "use tokens; case-insensitive YES/NO-only answers use yesno. Do not mark non_unique as tokens. "
        "Describe input_schema with the small token grammar: int nodes require bounds, strings use "
        "length/alphabet, arrays require length and one scalar child, repeat requires length/children. "
        "Graph nodes consume unweighted u v pairs, length=edge count, vertices=vertex count, "
        "minimum=first vertex (default 1). tree requires connected acyclic n-1 edges. "
        "Expressions use previously read integer names and + - * // % only; NO eval/functions. "
        "total_constraints field refers to an int node (sum values across repeats) or array/string "
        "node (sum lengths). All sums are per input file. Use validation_warnings for every semantic "
        "condition not represented by this grammar; use [] plus warnings if format cannot be represented. "
        "Do not silently ignore constraints.\nDATA:\n"
        + json.dumps(
            {"metadata": metadata, "statement": statement.model_dump()},
            ensure_ascii=False,
        )
    )
    return stage_document(
        directory / "problem_spec.json", ProblemSpec, llm, "spec", prompt
    )


def prepare_plan(
    directory: Path,
    statement: Statement,
    spec: ProblemSpec,
    cfg: Config,
    llm: LLMClient,
) -> GeneratorPlan:
    prompt = (
        "Stage B: plan tests BEFORE generating code. Design 10-30 files when meaningful, "
        f"at least 6 and at most {cfg.sandbox.max_tests}. Cover small, boundary, random, structured, "
        "large, maximum/near-maximum; include adversarial cases suited to THIS problem. "
        "Do not mechanically use irrelevant graph/array patterns. One test id is one complete "
        "valid input file, respecting ALL sum limits across its cases. Avoid semantically duplicate "
        f"files. Each file <= {cfg.sandbox.file_limit_bytes} UTF-8 bytes, total inputs+outputs "
        f"<= {cfg.sandbox.total_limit_bytes} bytes. Seed={cfg.sandbox.seed}.\nDATA:\n"
        + json.dumps(
            {"statement": statement.model_dump(), "spec": spec.model_dump()},
            ensure_ascii=False,
        )
    )
    plan = stage_document(
        directory / "generator_plan.json", GeneratorPlan, llm, "plan", prompt
    )
    if len(plan.tests) > cfg.sandbox.max_tests:
        raise ValueError("Generator plan exceeds sandbox.max_tests")
    return plan


def prepare_code(
    directory: Path,
    statement: Statement,
    spec: ProblemSpec,
    plan: GeneratorPlan,
    cfg: Config,
    llm: LLMClient,
    *,
    repair: str = "",
) -> None:
    if (
        not repair
        and (directory / "gen.cpp").exists()
        and (directory / "validator.cpp").exists()
    ):
        return
    prompt = (
        "Stage C: write standalone C++20 generator and an independent C++20 input validator. "
        "No testlib or external headers. Generator invocation: main <test_id> <seed>. "
        "Write exactly ONE complete input file to stdout, nothing else. Seed with the supplied "
        "seed only (never clock/random_device); output must be byte-reproducible. Do not write files, "
        "read system files, run shell, fork, use networking or expect secrets. Validator reads the "
        "complete input from stdin and returns 0 iff ALL format/semantic/aggregate constraints hold, "
        "otherwise nonzero and a brief diagnostic on stderr. Validator must check EOF, not solve "
        "the problem or read generator output from any file. Derive validation independently from "
        "the original statement, including constraints not covered by input_schema. "
        f"Generator output <= {cfg.sandbox.file_limit_bytes} bytes per file; "
        f"all files+reference outputs <= {cfg.sandbox.total_limit_bytes} bytes.\nDATA:\n"
        + json.dumps(
            {
                "statement": statement.model_dump(),
                "spec": spec.model_dump(),
                "plan": plan.model_dump(),
            },
            ensure_ascii=False,
        )
    )
    if repair:
        prompt += (
            "\nRepair the previous generator/validator after this diagnostic:\n"
            + repair[:5000]
        )
        for name in ("gen.cpp", "validator.cpp"):
            prompt += "\nPrevious " + name + ":\n" + (directory / name).read_text()
    value = llm.generate(
        "repair" if repair else "code", prompt, GeneratedCode, directory
    )
    write_text(directory / "gen.cpp", value.generator_cpp)
    write_text(directory / "validator.cpp", value.validator_cpp)


class GenerationError(ValueError):
    pass


class ReferenceError(ValueError):
    pass


class CheckerError(ValueError):
    pass


def prepare_checker(
    directory: Path,
    statement: Statement,
    spec: ProblemSpec,
    llm: LLMClient,
    *,
    repair: str = "",
) -> None:
    data = json.dumps(
        {"statement": statement.model_dump(), "spec": spec.model_dump()},
        ensure_ascii=False,
    )
    stage_document(
        directory / "checker_tests.json",
        NonUniqueCheckerExamples
        if spec.output_mode == "non_unique"
        else CheckerExamples,
        llm,
        "checker_tests",
        "Stage D examples: derive independent semantic checker tests directly from the statement. "
        "List EVERY rule for a valid contestant output. Provide 3-6 TINY legal complete inputs "
        "with mathematically verified valid outputs and 2-6 invalid outputs EACH with reasons. "
        "Cover missing/excess tokens, malformed tokens, output bounds, and each semantic rule. "
        "For non_unique include at least one input with TWO DIFFERENT token sequences that are "
        "both correct, including a different objective/common sum where permitted. Never assume "
        "a chosen reference answer is the only valid answer. For floating use the exact stated "
        "absolute/relative tolerances and cases inside/outside them. Do not invent tolerances. "
        "You have not seen the checker implementation; calculate examples independently.\nDATA:\n"
        + data,
    )
    if not repair and (directory / "checker.cpp").exists():
        return
    prompt = (
        "Stage D checker: write a standalone C++20 semantic output checker, NO testlib/external "
        "headers. Invocation: main <input_file> <contestant_output_file> <jury_answer_file>. "
        "Read ONLY these three paths. Exit 0 for accepted, 1 for wrong/malformed contestant output, "
        "3 for internal error/invalid input or jury answer. No stdout, brief stderr diagnostics. "
        "Check ALL output semantics, numeric bounds, token parsing overflow, missing tokens and EOF "
        "(allow trailing whitespace only). Use safe wide arithmetic for sums/products. "
        "For non_unique validate the contestant construction against input constraints and accept "
        "ALL valid solutions, including solutions differing from the jury's objective/common sum "
        "where allowed; never require token equality to jury. For optimization check the objective "
        "against the jury ONLY if the problem requires it. For floating implement precisely the "
        "stated tolerance, reject NaN/Inf, never invent a tolerance. No network, shell, subprocesses, "
        "system files, random choices or secrets. Derive semantics directly from the original "
        "statement independently of generator/validator/reference; implement efficient checks for "
        "maximum-size input under 10 seconds CPU and 512 MiB.\nDATA:\n" + data
    )
    if repair:
        prompt += "\nFailure diagnostic:\n" + repair[:5000]
        prompt += "\nPrevious checker.cpp:\n" + (directory / "checker.cpp").read_text()
    value = llm.generate(
        "checker_repair" if repair else "checker", prompt, GeneratedChecker, directory
    )
    write_text(directory / "checker.cpp", value.checker_cpp)


class Sandbox:
    """Reuse MiniOJ's no-network Docker sandbox. Mount source/binary ONLY."""

    def __init__(self, cfg: Config, directory: Path):
        from minioj.judge.runner import DockerJudge

        self.cfg, self.directory = cfg, directory
        self.owner = "cf-import-" + digest(str(cfg.state_file))[:16]
        self.judge = DockerJudge(image=cfg.sandbox.image, owner=self.owner)

    def result(self, result, label, *, generation: bool = True):
        error_type = GenerationError if generation else ReferenceError
        write_json(self.directory / "logs" / (label + ".json"), vars(result))
        if (
            result.exit_code != 0
            or result.timed_out
            or result.oom_killed
            or result.output_exceeded
            or not result.stdout_valid_utf8
            or result.stdout_truncated
            or result.stderr_truncated
        ):
            raise error_type(
                f"{label} failed: exit={result.exit_code}, timeout={result.timed_out}, "
                f"oom={result.oom_killed}, output_limit={result.output_exceeded}; "
                + result.stderr[:2000]
            )
        return result.stdout

    def compile(self, work: Path, source: str, label: str, *, generation: bool = True):
        return self.result(
            self.judge.compile(work, source, self.cfg.sandbox.memory_mb),
            label,
            generation=generation,
        )

    def run(
        self,
        work: Path,
        text: str,
        label: str,
        *,
        args=None,
        generation=True,
        time_limit_ms=None,
        memory_mb=None,
    ):
        return self.result(
            self.judge.execute(
                work,
                text,
                time_limit_ms or self.cfg.sandbox.time_limit_ms,
                memory_mb or self.cfg.sandbox.memory_mb,
                arguments=args,
                output_limit=self.cfg.sandbox.file_limit_bytes,
            ),
            label,
            generation=generation,
        )

    def build(
        self,
        candidates: list[Path],
        statement: Statement,
        spec: ProblemSpec,
        plan: GeneratorPlan,
    ) -> tuple[Path, dict]:
        from minioj.config import settings
        from minioj.judge.checker import outputs_match

        if spec.output_mode == "interactive":
            raise ReferenceError(
                f"Unsupported checker requirement: {spec.output_mode}; no tests uploaded"
            )
        self.judge.ensure_available()
        self.judge.cleanup_owned_containers()
        # DockerJudge metrics use its configured job directory, which never gets mounted with secrets.
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        sandbox_root = self.directory / "sandbox"
        sandbox_root.mkdir(exist_ok=True)
        errors = []
        with tempfile.TemporaryDirectory(prefix="build-", dir=sandbox_root) as temp:
            root = Path(temp)
            root.chmod(0o755)
            gen, validator, reference, checker = [
                root / n for n in ("generator", "validator", "reference", "checker")
            ]
            for p in (gen, validator, reference, checker):
                p.mkdir()
            self.compile(
                gen, (self.directory / "gen.cpp").read_text(), "generator-compile"
            )
            label = self.directory.parent.name + self.directory.name
            print(f"[{label}] generator compiled", flush=True)
            self.compile(
                validator,
                (self.directory / "validator.cpp").read_text(),
                "validator-compile",
            )
            harness = None
            if spec.output_mode in {"non_unique", "floating"}:
                from .checkers import CheckerHarness

                (self.directory / "checker_validation.json").unlink(missing_ok=True)
                harness = CheckerHarness(self, checker, spec)
                for i, sample in enumerate(statement.samples, 1):
                    harness.check(
                        sample.input,
                        sample.output,
                        sample.output,
                        f"checker-official-sample-{i}",
                    )
            chosen = None
            compiled_source = None
            for number, source in enumerate(candidates, 1):
                try:
                    source_text = source.read_bytes().decode("utf-8")
                    self.compile(
                        reference,
                        source_text,
                        f"reference-{number}-compile",
                        generation=False,
                    )
                    for i, sample in enumerate(statement.samples, 1):
                        validate_input(sample.input, spec)
                        self.run(validator, sample.input, f"sample-{i}-validator")
                        actual = self.run(
                            reference,
                            sample.input,
                            f"reference-{number}-sample-{i}",
                            generation=False,
                            time_limit_ms=statement.time_limit_ms,
                            memory_mb=statement.memory_limit_mb,
                        )
                        if harness:
                            harness.check(
                                sample.input,
                                actual,
                                sample.output,
                                f"checker-reference-{number}-sample-{i}",
                                error_type=ReferenceError,
                            )
                        elif not outputs_match(actual, sample.output, spec.output_mode):
                            raise ReferenceError(
                                "Local solution disagrees with an official sample"
                            )
                    chosen = source
                    compiled_source = source_text
                    break
                except ReferenceError as exc:
                    errors.append({"source": str(source), "error": str(exc)})
            write_json(self.directory / "reference_candidates.json", errors)
            if chosen is None:
                raise ReferenceError(
                    "No local reference compiled and passed official samples"
                )
            print(
                f"[{label}] reference compiled and passed samples: {chosen.name}",
                flush=True,
            )
            if harness:
                harness.selftest(validator, reference, statement)
                print(
                    f"[{label}] checker compiled; positive/negative/alternative-answer checks passed",
                    flush=True,
                )
            tests = self.directory / "tests"
            tests.mkdir(exist_ok=True)
            # Old sets never count as ready without a fresh manifest.
            (self.directory / "tests_manifest.json").unlink(missing_ok=True)
            for path in list(tests.glob("*.in")) + list(tests.glob("*.out")):
                path.unlink()
            entries, total, fingerprints = [], 0, set()
            for item in plan.tests:
                args = [str(item.id), str(self.cfg.sandbox.seed)]
                text = self.run(gen, "", f"test-{item.id}-generate", args=args)
                if text != self.run(gen, "", f"test-{item.id}-reproduce", args=args):
                    raise GenerationError(
                        "Generator is not reproducible with the fixed seed"
                    )
                try:
                    validate_input(text, spec)
                except ValueError as exc:
                    raise GenerationError(str(exc)) from exc
                self.run(validator, text, f"test-{item.id}-validate")
                fingerprint = digest(" ".join(text.split()))
                if fingerprint in fingerprints:
                    raise GenerationError(
                        "Generator produced duplicate token-equivalent test files"
                    )
                fingerprints.add(fingerprint)
                output = self.run(
                    reference,
                    text,
                    f"test-{item.id}-reference",
                    generation=False,
                    time_limit_ms=statement.time_limit_ms,
                    memory_mb=statement.memory_limit_mb,
                )
                if not output.strip():
                    raise ReferenceError("Reference produced an empty expected output")
                if harness:
                    harness.check(text, output, output, f"checker-generated-{item.id}")
                total += len(text.encode()) + len(output.encode())
                if total > self.cfg.sandbox.total_limit_bytes:
                    raise GenerationError(
                        "Total test data size exceeded configured limit"
                    )
                filename = f"{item.id:02d}"
                write_text(tests / (filename + ".in"), text)
                write_text(tests / (filename + ".out"), output)
                entries.append(
                    {
                        "name": filename,
                        "category": item.category,
                        "description": item.description,
                        "input_sha256": digest(text),
                        "output_sha256": digest(output),
                    }
                )
            if chosen.read_bytes().decode("utf-8") != compiled_source:
                raise ReferenceError(
                    "Reference source changed during generation; rerun with stable source"
                )
            manifest = {
                "tests": entries,
                "total_bytes": total,
                "source_path": str(chosen),
                "source_sha256": digest(chosen.read_bytes()),
                "seed": self.cfg.sandbox.seed,
                "validation_warnings": spec.validation_warnings,
            }
            if harness:
                manifest["checker_sha256"] = harness.save_report()
            write_json(self.directory / "tests_manifest.json", manifest)
            return chosen, manifest
