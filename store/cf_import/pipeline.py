from __future__ import annotations

import json
from pathlib import Path

from .codeforces import CodeforcesClient
from .config import Config
from .contracts import ProblemSpec
from .generation import (
    CheckerError,
    GenerationError,
    Sandbox,
    prepare_checker,
    prepare_code,
    prepare_plan,
    prepare_spec,
)
from .llm import LLMClient
from .minioj import MiniOJClient
from .scanner import REFERENCE_SELECTION_POLICY, ProblemId, scan
from .statement import StatementProvider
from .storage import StateStore, digest, now, read_json, write_json
from .validation import validate_input


def log(pid: ProblemId, message: str):
    print(f"[{pid.external_id}] {message}", flush=True)


def json_digest(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False))


def dependencies(
    directory: Path, cfg: Config, source: Path, candidates: list[Path]
) -> str:
    return json_digest(
        {
            "files": {
                n: digest((directory / n).read_bytes())
                for n in (
                    "statement.json",
                    "problem_spec.json",
                    "generator_plan.json",
                    "gen.cpp",
                    "validator.cpp",
                    "checker.cpp",
                    "checker_tests.json",
                )
                if (directory / n).exists()
            },
            "source": str(source),
            "source_sha256": digest(source.read_bytes()),
            "reference_selection": {
                "policy": REFERENCE_SELECTION_POLICY,
                "candidates": [
                    {"path": str(p), "sha256": digest(p.read_bytes())}
                    for p in candidates
                ],
            },
            "sandbox": cfg.sandbox.model_dump(),
        }
    )


def check_tests(
    directory: Path, manifest: dict, spec: ProblemSpec, cfg: Config
) -> None:
    if spec.output_mode in {"non_unique", "floating"}:
        from .checkers import checker_bundle

        report = read_json(directory / "checker_validation.json")
        if (
            manifest.get("checker_sha256")
            != digest(checker_bundle(directory).serialize())
            or report.get("bundle_sha256") != manifest["checker_sha256"]
            or report.get("examples_sha256")
            != digest((directory / "checker_tests.json").read_bytes())
            or not report.get("checks")
            or not all(c.get("passed") for c in report["checks"])
        ):
            raise ValueError("Special checker validation is missing or modified")
    total = 0
    names = []
    for item in manifest["tests"]:
        name = item["name"]
        if not name.isdecimal() or len(name) != 2:
            raise ValueError("Unsafe test filename in manifest")
        names.append(name)
        for suffix, key in (("in", "input_sha256"), ("out", "output_sha256")):
            path = directory / "tests" / (name + "." + suffix)
            if path.is_symlink():
                raise ValueError("Test symlinks are not allowed")
            raw = path.read_bytes()
            if (
                not raw.strip()
                or len(raw) > cfg.sandbox.file_limit_bytes
                or digest(raw) != item[key]
            ):
                raise ValueError("Missing, modified, empty or oversized test artifact")
            text = raw.decode("utf-8")
            if suffix == "in":
                validate_input(text, spec)
            total += len(raw)
    if (
        not names
        or len(names) != len(set(names))
        or total > cfg.sandbox.total_limit_bytes
    ):
        raise ValueError("Invalid test manifest/total size")
    if {p.stem for p in (directory / "tests").glob("*.in")} != set(names):
        raise ValueError("Input files disagree with manifest")
    if {p.stem for p in (directory / "tests").glob("*.out")} != set(names):
        raise ValueError("Output files disagree with manifest")


def problem_payload(
    pid: ProblemId, metadata: dict, statement, spec, directory: Path
) -> dict:
    payload = {
        "id": pid.minioj_id,
        "title": metadata["name"],
        "statement": statement.statement,
        "input_specification": statement.input_format,
        "output_specification": statement.output_format,
        "notes": statement.notes,
        "time_limit_ms": statement.time_limit_ms,
        "memory_limit_mb": statement.memory_limit_mb,
        "source": "Codeforces",
        "source_id": pid.external_id,
        "source_url": pid.url,
        "rating": metadata.get("rating"),
        "tags": ",".join(metadata.get("tags", [])),
        "checker": "testlib"
        if spec.output_mode in {"non_unique", "floating"}
        else spec.output_mode,
    }
    if payload["checker"] == "testlib":
        payload["checker_source"] = (directory / "checker.cpp").read_text()
    return payload


class Importer:
    def __init__(
        self,
        cfg: Config,
        *,
        cf=None,
        statements=None,
        llm=None,
        minioj=None,
        sandbox_factory=Sandbox,
    ):
        self.cfg = cfg
        self.state = StateStore(cfg.state_file)
        self.cf = cf or CodeforcesClient(cfg)
        self.statements = statements or StatementProvider(cfg)
        self.llm = llm or LLMClient(cfg.llm)
        self.minioj = minioj or MiniOJClient(cfg.minioj)
        self.sandbox_factory = sandbox_factory

    def discover(self):
        problems, records = scan(self.cfg.source_dir)
        write_json(
            self.cfg.cache_dir / "scan.json", {"scanned_at": now(), "files": records}
        )
        for pid, sources in problems.items():
            entry = self.state.entry(pid.key)
            self.state.update(
                pid.key,
                source_paths=[str(p) for p in sources],
                last_error=entry.get("last_error"),
            )
        for r in records:
            if r["status"] == "unresolved":
                print(f"[unresolved] {r['source_path']}: {r['reason']}", flush=True)
        return problems, records

    def sync(self, *, refresh=False):
        problems, records = self.discover()
        accepted, account = self.cf.sync_ac(refresh=refresh)
        for pid in problems:
            entry = self.state.entry(pid.key)
            if pid not in accepted:
                self.state.update(
                    pid.key, "skipped_not_ac", ac_account=account["handle"]
                )
            elif entry["status"] not in {"verified", "import_success"}:
                self.state.update(pid.key, "ac_confirmed", ac_account=account["handle"])
        print(
            f"AC history cached at {account['fetched_at']}: {len(accepted)} AC problems, "
            f"{len(set(problems) & accepted)} with local solutions",
            flush=True,
        )
        return problems, records, accepted

    def process(
        self,
        pid: ProblemId,
        candidates: list[Path],
        metadata: dict,
        *,
        dry_run: bool,
        force: bool,
        retry_statements: bool,
    ) -> str:
        cfg = self.cfg
        directory = cfg.generated_dir / str(pid.contest_id) / pid.index
        entry = self.state.entry(pid.key)
        log(pid, "AC confirmed; metadata cached")
        self.state.update(pid.key, "metadata_ready", last_error=None)
        statement = self.statements.get(pid, metadata, retry_failed=retry_statements)
        self.state.update(pid.key, "statement_ready")
        log(pid, "statement cached")
        context = json_digest(
            {"metadata": metadata, "statement": statement.model_dump()}
        )
        if force or (entry.get("context") and entry["context"] != context):
            for name in (
                "problem_spec.json",
                "generator_plan.json",
                "gen.cpp",
                "validator.cpp",
                "checker.cpp",
                "checker_tests.json",
                "checker_validation.json",
                "tests_manifest.json",
            ):
                (directory / name).unlink(missing_ok=True)
            self.state.update(
                pid.key,
                repair_count=0,
                generation_exhausted=False,
                repair_diagnostic=None,
                checker_repair_count=0,
                checker_exhausted=False,
                checker_repair_diagnostic=None,
            )
        self.state.update(pid.key, context=context)
        spec = prepare_spec(directory, statement, metadata, self.llm)
        if spec.output_mode == "interactive":
            raise ValueError(f"Unsupported {spec.output_mode} checker; no upload")
        self.state.update(pid.key, "spec_ready")
        log(pid, "problem spec ready")
        for warning in spec.validation_warnings:
            log(pid, "WARNING: " + warning)
        special = spec.output_mode in {"non_unique", "floating"}
        if special:
            prepare_checker(directory, statement, spec, self.llm)
            self.state.update(pid.key, "checker_ready")
            log(pid, "LLM checker and independent examples ready")
        plan = prepare_plan(directory, statement, spec, cfg, self.llm)
        self.state.update(pid.key, "plan_ready")
        prepare_code(directory, statement, spec, plan, cfg, self.llm)
        self.state.update(pid.key, "generator_ready")
        reference_override = cfg.references.get(pid.external_id)
        if reference_override:
            candidate = Path(reference_override).expanduser()
            candidate = (
                candidate
                if candidate.is_absolute()
                else Path(__file__).resolve().parents[2] / candidate
            )
            candidate = candidate.resolve()
            if candidate not in [p.resolve() for p in candidates]:
                raise ValueError(
                    "references override must be one of this problem's scanned solutions"
                )
            candidates = [candidate]
        manifest_path = directory / "tests_manifest.json"
        ready = False
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            reference = Path(manifest["source_path"])
            if (
                reference in candidates
                and reference.exists()
                and manifest.get("dependencies")
                == dependencies(directory, cfg, reference, candidates)
            ):
                check_tests(directory, manifest, spec, cfg)
                ready = True
        if not ready:

            def checker_fingerprint():
                return json_digest(
                    {
                        "source": digest((directory / "checker.cpp").read_bytes()),
                        "examples": digest(
                            (directory / "checker_tests.json").read_bytes()
                        ),
                        "context": context,
                        "sandbox": cfg.sandbox.model_dump(),
                    }
                )

            if special:
                fingerprint = checker_fingerprint()
                if entry.get("checker_fingerprint") != fingerprint:
                    self.state.update(
                        pid.key,
                        checker_repair_count=0,
                        checker_exhausted=False,
                        checker_repair_diagnostic=None,
                        checker_fingerprint=fingerprint,
                    )
                if entry.get("checker_exhausted"):
                    raise ValueError(
                        "Checker repair budget exhausted; inspect/edit checker artifacts or use --force"
                    )
                if entry.get("checker_repair_diagnostic"):
                    prepare_checker(
                        directory,
                        statement,
                        spec,
                        self.llm,
                        repair=entry["checker_repair_diagnostic"],
                    )
                    self.state.update(
                        pid.key,
                        checker_repair_diagnostic=None,
                        checker_fingerprint=checker_fingerprint(),
                    )
            attempt_fingerprint = json_digest(
                {
                    "generator": digest((directory / "gen.cpp").read_bytes()),
                    "validator": digest((directory / "validator.cpp").read_bytes()),
                    "candidates": {str(p): digest(p.read_bytes()) for p in candidates},
                    "context": context,
                    "sandbox": cfg.sandbox.model_dump(),
                }
            )
            if entry.get("generation_fingerprint") != attempt_fingerprint:
                self.state.update(
                    pid.key,
                    repair_count=0,
                    generation_exhausted=False,
                    repair_diagnostic=None,
                    generation_fingerprint=attempt_fingerprint,
                )
            if entry.get("generation_exhausted"):
                raise ValueError(
                    "Generation repair budget exhausted; inspect/edit artifacts or use --force"
                )
            if entry.get("repair_diagnostic"):
                prepare_code(
                    directory,
                    statement,
                    spec,
                    plan,
                    cfg,
                    self.llm,
                    repair=entry["repair_diagnostic"],
                )
                self.state.update(pid.key, repair_diagnostic=None)
            while True:
                try:
                    reference, manifest = self.sandbox_factory(cfg, directory).build(
                        candidates, statement, spec, plan
                    )
                    break
                except CheckerError as exc:
                    log(pid, "checker validation failed: " + str(exc)[:500])
                    used = entry.get("checker_repair_count", 0)
                    if used >= cfg.llm.repair_attempts:
                        self.state.update(pid.key, checker_exhausted=True)
                        raise
                    self.state.update(
                        pid.key,
                        checker_repair_count=used + 1,
                        checker_repair_diagnostic=str(exc)[:5000],
                    )
                    prepare_checker(
                        directory, statement, spec, self.llm, repair=str(exc)
                    )
                    self.state.update(
                        pid.key,
                        checker_repair_diagnostic=None,
                        checker_fingerprint=checker_fingerprint(),
                    )
                except GenerationError as exc:
                    log(pid, "generator/validation failed: " + str(exc)[:500])
                    used = entry.get("repair_count", 0)
                    if used >= cfg.llm.repair_attempts:
                        self.state.update(pid.key, generation_exhausted=True)
                        raise
                    # Persist pending repair before requesting the LLM, including interrupted repairs.
                    self.state.update(
                        pid.key,
                        repair_count=used + 1,
                        repair_diagnostic=str(exc)[:5000],
                    )
                    prepare_code(
                        directory, statement, spec, plan, cfg, self.llm, repair=str(exc)
                    )
                    attempt_fingerprint = json_digest(
                        {
                            "generator": digest((directory / "gen.cpp").read_bytes()),
                            "validator": digest(
                                (directory / "validator.cpp").read_bytes()
                            ),
                            "candidates": {
                                str(p): digest(p.read_bytes()) for p in candidates
                            },
                            "context": context,
                            "sandbox": cfg.sandbox.model_dump(),
                        }
                    )
                    self.state.update(
                        pid.key,
                        repair_diagnostic=None,
                        generation_fingerprint=attempt_fingerprint,
                    )
            manifest["dependencies"] = dependencies(
                directory, cfg, reference, candidates
            )
            manifest["reference_selection_policy"] = REFERENCE_SELECTION_POLICY
            manifest["reference_candidates"] = [str(p) for p in candidates]
            write_json(manifest_path, manifest)
        check_tests(directory, manifest, spec, cfg)
        reference_code = reference.read_bytes().decode("utf-8")
        if digest(reference_code) != manifest.get("source_sha256"):
            raise ValueError(
                "Reference source changed since test generation; regenerate before upload"
            )
        self.state.update(pid.key, "tests_ready")
        log(pid, f"generated {len(manifest['tests'])} tests; validation passed")
        if dry_run:
            self.state.update(pid.key, "tests_ready", result="dry_run_success")
            return "dry_run_success"
        payload = problem_payload(pid, metadata, statement, spec, directory)
        existing = self.minioj.find(pid.external_id, pid.minioj_id)
        remote_tests = self.minioj.testcases(existing["problem_id"]) if existing else []
        desired = [
            {"type": "sample", "input": s.input, "output": s.output}
            for s in statement.samples
        ]
        desired += [
            {
                "type": "generated",
                "input": (directory / "tests" / (t["name"] + ".in")).read_text(),
                "output": (directory / "tests" / (t["name"] + ".out")).read_text(),
            }
            for t in manifest["tests"]
        ]
        for t in desired:
            t.update(input_sha256=digest(t["input"]), output_sha256=digest(t["output"]))
        upload_digest = json_digest(
            {
                "problem": payload,
                "tests": desired,
                "reference": digest(reference_code),
            }
        )

        def signature(t):
            return (t["type"], t.get("input_sha256"), t.get("output_sha256"))

        desired_set = {signature(t) for t in desired}
        remote_set = {signature(t) for t in remote_tests}
        same_target = entry.get("minioj_base_url") == cfg.minioj.base_url.rstrip("/")
        if (
            not force
            and existing
            and same_target
            and entry.get("verified_upload_digest") == upload_digest
            and desired_set <= remote_set
        ):
            # Also detect edits to metadata/limits/statement after successful verification.
            detail = self.minioj.request("GET", "/problems/" + existing["problem_id"])
            unchanged = all(
                detail.get(k) == payload[k]
                for k in (
                    "title",
                    "statement",
                    "input_specification",
                    "output_specification",
                    "notes",
                    "source",
                    "source_id",
                    "source_url",
                    "rating",
                    "checker",
                )
            )
            unchanged &= detail.get("limits") == {
                "time_ms": payload["time_limit_ms"],
                "memory_mb": payload["memory_limit_mb"],
            }
            unchanged &= detail.get("tags") == metadata.get("tags", [])
            if special:
                checker_meta = self.minioj.request(
                    "GET", f"/admin/problems/{existing['problem_id']}/checker"
                )
                unchanged &= checker_meta.get("sha256") == manifest["checker_sha256"]
            if unchanged and remote_set == {
                tuple(t) for t in entry.get("verified_remote_tests", [])
            }:
                self.state.update(pid.key, "verified", result="import_success")
                log(pid, "already imported and verified; skipped")
                return "already_imported"
        problem_id = self.minioj.ensure_problem(payload, existing)
        if special:
            checker_meta = self.minioj.request(
                "GET", f"/admin/problems/{problem_id}/checker"
            )
            if (
                checker_meta.get("checker") != "testlib"
                or checker_meta.get("sha256") != manifest["checker_sha256"]
            ):
                raise ValueError("MiniOJ did not store the validated checker bundle")
        self.state.update(
            pid.key,
            "uploading",
            problem_id=problem_id,
            minioj_base_url=cfg.minioj.base_url.rstrip("/"),
        )
        # Only remove receipts owned by this importer; preserve unrelated/manual remote tests.
        owned = set(entry.get("owned_testcase_ids", [])) if same_target else set()
        for remote in remote_tests:
            if remote["id"] in owned and signature(remote) not in desired_set:
                self.minioj.request(
                    "DELETE", f"/admin/problems/{problem_id}/testcases/{remote['id']}"
                )
                owned.remove(remote["id"])
        remote_tests = self.minioj.testcases(problem_id)
        remote_set = {signature(t) for t in remote_tests}
        for testcase in desired:
            if signature(testcase) in remote_set:
                continue
            added = self.minioj.request(
                "POST", f"/admin/problems/{problem_id}/testcases", testcase
            )
            owned.add(added["id"])
            remote_set.add(signature(testcase))
            self.state.update(pid.key, owned_testcase_ids=sorted(owned))
        self.state.update(
            pid.key,
            "uploaded",
            owned_testcase_ids=sorted(owned),
            upload_digest=upload_digest,
        )
        log(pid, "uploaded to MiniOJ")
        submission_id = (
            entry.get("submission_id")
            if same_target
            and entry.get("submission_upload_digest") == upload_digest
            and not force
            else None
        )
        if submission_id:
            previous = self.minioj.request("GET", f"/submissions/{submission_id}")
            if previous.get("status") == "FINISHED":
                submission_id = (
                    None  # A prior failed/modified import needs a new validation.
                )
        if submission_id is None:
            response = self.minioj.request(
                "POST",
                "/submissions",
                {
                    "problem_id": problem_id,
                    "language": "cpp20",
                    "source_code": reference_code,
                },
            )
            submission_id = response["submission_id"]
            self.state.update(
                pid.key,
                submission_id=submission_id,
                submission_upload_digest=upload_digest,
            )
        result = self.minioj.verify(submission_id)
        write_json(directory / "minioj_verification.json", result)
        if result.get("verdict") != "AC":
            self.state.update(
                pid.key,
                "minioj_validation_failed",
                result="minioj_validation_failed",
                last_error=f"MiniOJ returned {result.get('verdict')}",
            )
            log(pid, "MiniOJ validation failed: " + str(result.get("verdict")))
            return "minioj_validation_failed"
        remote_tests = self.minioj.testcases(problem_id)
        self.state.update(
            pid.key,
            "verified",
            result="import_success",
            verified_upload_digest=upload_digest,
            verified_remote_tests=[list(signature(t)) for t in remote_tests],
        )
        log(pid, "MiniOJ verification AC")
        return "import_success"

    def run(
        self,
        *,
        problem: ProblemId | None = None,
        limit: int | None = None,
        dry_run=False,
        force=False,
        refresh_ac=False,
        retry_statements=False,
    ) -> dict:
        problems, records, accepted = self.sync(refresh=refresh_ac)
        if problem and problem not in problems:
            raise ValueError("Selected problem has no resolvable local solution")
        selected = [
            p for p in problems if p in accepted and (problem is None or p == problem)
        ]
        if limit is not None:
            selected = selected[:limit]
        if selected and not dry_run:
            self.minioj.ensure_admin()
        metadata = self.cf.metadata(selected)
        summary = {
            "total_local_solutions": len(records),
            "local_problems": len(problems),
            "ac_problems": len(set(problems) & accepted),
            "selected": len(selected),
            "imported": 0,
            "dry_run_success": 0,
            "already_imported": 0,
            "skipped_not_ac": len(set(problems) - accepted),
            "failed": 0,
            "minioj_validation_failed": 0,
            "unresolved": sum(r["status"] == "unresolved" for r in records),
        }
        for pid in selected:
            directory = self.cfg.generated_dir / str(pid.contest_id) / pid.index
            try:
                if pid not in metadata:
                    raise ValueError(
                        "Problem missing from official metadata cache; no identity guess permitted"
                    )
                outcome = self.process(
                    pid,
                    problems[pid],
                    metadata[pid],
                    dry_run=dry_run,
                    force=force,
                    retry_statements=retry_statements,
                )
                summary["imported" if outcome == "import_success" else outcome] += 1
            except Exception as exc:  # noqa: BLE001 -- isolate per-problem failures
                summary["failed"] += 1
                self.state.update(
                    pid.key, "failed", result="failed", last_error=str(exc)[:5000]
                )
                log(pid, "FAILED: " + str(exc)[:1000])
            finally:
                write_json(directory / "import_result.json", self.state.entry(pid.key))
        write_json(self.cfg.cache_dir / "last_summary.json", summary)
        return summary
