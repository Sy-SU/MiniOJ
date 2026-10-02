from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from scripts.cf_import_fixture import FIXTURES, METADATA, PID, PLAN, SPEC, UpstreamHTTP
from store.cf_import.codeforces import CodeforcesClient
from store.cf_import.config import Config
from store.cf_import.contracts import GeneratorPlan, ProblemSpec
from store.cf_import.generation import GenerationError
from store.cf_import.http import HTTPClient, HTTPError, RequestPacer
from store.cf_import.llm import LLMClient
from store.cf_import.minioj import MiniOJClient
from store.cf_import.pipeline import Importer
from store.cf_import.scanner import parse_problem, scan
from store.cf_import.statement import StatementProvider, parse_statement
from store.cf_import.storage import (
    StateStore,
    digest,
    importer_lock,
    read_json,
    write_json,
    write_text,
)
from store.cf_import.validation import expression, validate_input


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cfg = Config(
        source_dir=tmp_path / "sources",
        generated_dir=tmp_path / "generated",
        cache_dir=tmp_path / "cache",
        state_file=tmp_path / "state.json",
    )
    cfg.codeforces.handle = "FixtureUser"
    cfg.llm.base_url = "https://fixture.invalid/v1"
    cfg.llm.model = "fixture"
    cfg.minioj.base_url = "http://testserver"
    cfg.minioj.verification_timeout_seconds = 0.01
    cfg.llm.repair_attempts = 0
    source = cfg.source_dir / str(PID.contest_id) / "A.cpp"
    source.parent.mkdir(parents=True)
    source.write_text((FIXTURES / "reference.cpp").read_text())
    monkeypatch.setenv(cfg.llm.api_key_env, "fixture-key")
    monkeypatch.setattr(RequestPacer, "wait", lambda *args: None)
    return cfg


def test_scanner_uses_paths_not_contents(tmp_path):
    names = [
        "1791/C.cpp",
        "1791/C__Good.cpp",
        "1791/C__Fast.cpp",
        "1791/C__good.cpp",
        "1791/C__Generator.cpp",
        "1904_A.cpp",
        "Codeforces_1791_C.cpp",
        "1791/1904A.cpp",
        "1791/B3742.cpp",
        "1791/demo.cpp",
    ]
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("This claims to be 1000A; never trust contents")
    groups, records = scan(tmp_path)
    assert set(groups) == {parse_problem("1791C"), parse_problem("1904A")}
    paths = groups[parse_problem("1791C")]
    assert [p.name for p in paths] == [
        "C.cpp",
        "Codeforces_1791_C.cpp",
        "C__Fast.cpp",
        "C__Good.cpp",
        "C__good.cpp",
    ]
    assert sum(r["status"] == "unresolved" for r in records) == 3
    assert sum(r["status"] == "ignored_helper" for r in records) == 1


def test_scanner_rejects_symlink(tmp_path):
    (tmp_path / "1791C.cpp").symlink_to("/etc/passwd")
    groups, records = scan(tmp_path)
    assert not groups and records[0]["status"] == "unresolved"


def test_ac_history_paginates_caches_and_filters_account(cfg):
    cfg.codeforces.page_size = 2

    class History:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **kwargs):
            offset = kwargs["params"]["from"]
            self.calls.append(offset)
            if offset == 1:
                return {
                    "status": "OK",
                    "result": [
                        {
                            "id": i,
                            "verdict": verdict,
                            "author": {"members": [{"handle": cfg.codeforces.handle}]},
                            "problem": {"contestId": 1791, "index": index},
                        }
                        for i, verdict, index in [
                            (3, "WRONG_ANSWER", "A"),
                            (2, "OK", "C"),
                        ]
                    ],
                }
            return {"status": "OK", "result": []}

    http = History()
    client = CodeforcesClient(cfg, http)
    accepted, manifest = client.sync_ac()
    assert accepted == {parse_problem("1791C")}
    assert http.calls == [1, 3]
    assert manifest["submission_count"] == 2
    client.sync_ac()
    assert http.calls == [1, 3]
    cfg.codeforces.handle = "AnotherUser"
    client.sync_ac()
    assert http.calls == [1, 3, 1, 3]


def test_wrong_author_does_not_commit_ac_set(cfg):
    cfg.codeforces.handle = "SomeoneElse"
    with pytest.raises(ValueError, match="author"):
        CodeforcesClient(cfg, UpstreamHTTP()).sync_ac()
    assert not list(cfg.cache_dir.rglob("accepted.json"))


def test_statement_identity_samples_math_and_negative_cache(cfg):
    http = UpstreamHTTP()
    provider = StatementProvider(cfg, http)
    value = provider.get(PID, METADATA)
    assert value.samples[0].input == "2\n3\n1 2 3\n1\n-100\n"
    assert value.samples[0].output == "6\n-100\n"
    assert "$t$" in value.input_format
    assert value.notes == "The answer is unique."
    cache = cfg.generated_dir / str(PID.contest_id) / PID.index / "statement.json"
    write_json(cache, dict(value.model_dump(), notes="Note\n\n" + value.notes))
    assert provider.get(PID, METADATA).notes == "The answer is unique."
    assert read_json(cache)["notes"] == "The answer is unique."
    assert len(http.calls) == 1
    html = (FIXTURES / "statement.html").read_text().replace("A. Fixture", "B. Fixture")
    with pytest.raises(ValueError, match="disagrees"):
        parse_statement(html, PID, METADATA)
    (cfg.generated_dir / str(PID.contest_id) / PID.index / "statement.json").unlink()

    class Blocked:
        def request(self, *args, **kwargs):
            return "<html>access denied</html>"

    provider.http = Blocked()
    with pytest.raises(ValueError, match="problem-statement"):
        provider.get(PID, METADATA)
    with pytest.raises(ValueError, match="previously failed"):
        provider.get(PID, METADATA)


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
def test_llm_modes_validate_and_save_raw(cfg, mode):
    cfg.llm.api_mode = mode
    value = LLMClient(cfg.llm, UpstreamHTTP()).generate(
        "spec", "Stage A:", ProblemSpec, cfg.generated_dir
    )
    assert value.has_test_cases
    assert len(list((cfg.generated_dir / "llm_responses").glob("*.json"))) == 1


def test_llm_invalid_json_retries_are_bounded(cfg):
    class Invalid:
        count = 0

        def request(self, *args, **kwargs):
            self.count += 1
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"invalid":true}'},
                    }
                ]
            }

    http = Invalid()
    with pytest.raises(ValueError, match="after 3 attempts"):
        LLMClient(cfg.llm, http).generate(
            "spec", "Stage A:", ProblemSpec, cfg.generated_dir
        )
    assert http.count == 3


@pytest.mark.parametrize(
    "text, error",
    [
        ("0\n", "range"),
        ("1 2 0 101", "range"),
        ("1 2 0", "fewer"),
        ("1 1 5 garbage", "trailing"),
        ("", "Empty"),
        ("3 " + "1000 " + "0 " * 1000 + "1000 " + "0 " * 1000 + "1 0", "Total n"),
    ],
)
def test_input_validation_rejects_invalid_data(text, error):
    with pytest.raises(ValueError, match=error):
        validate_input(text, ProblemSpec.model_validate(SPEC))


def structural_spec(node):
    value = copy.deepcopy(SPEC)
    value.update(input_schema=[node], total_constraints=[])
    return ProblemSpec.model_validate(value)


def test_permutation_binary_strings_tree_and_dag():
    perm = structural_spec(
        {
            "kind": "array",
            "name": "p",
            "length": 3,
            "permutation": True,
            "children": [{"kind": "int", "name": "v", "minimum": 1, "maximum": 3}],
        }
    )
    validate_input("3 1 2", perm)
    with pytest.raises(ValueError, match="permutation"):
        validate_input("1 1 2", perm)
    binary = structural_spec(
        {"kind": "string", "name": "s", "length": 3, "alphabet": "01"}
    )
    with pytest.raises(ValueError, match="characters"):
        validate_input("012", binary)
    tree = structural_spec(
        {"kind": "graph", "name": "g", "vertices": 4, "length": 3, "tree": True}
    )
    validate_input("1 2 2 3 2 4", tree)
    with pytest.raises(ValueError, match="cycle"):
        validate_input("1 2 2 3 3 1", tree)
    with pytest.raises(ValueError, match="range"):
        validate_input("1 2 2 3 2 5", tree)
    dag = structural_spec(
        {
            "kind": "graph",
            "name": "g",
            "vertices": 3,
            "length": 3,
            "directed": True,
            "dag": True,
        }
    )
    with pytest.raises(ValueError, match="DAG"):
        validate_input("1 2 2 3 3 1", dag)


def test_expressions_cannot_execute_python():
    assert expression("n * 2 - 1", {"n": 3}) == 5
    with pytest.raises(ValueError, match="Unsupported"):
        expression("__import__('os').system('touch /tmp/no')", {})
    with pytest.raises(ValidationError):
        GeneratorPlan.model_validate(dict(PLAN, tests=PLAN["tests"][:5]))
    adversarial = copy.deepcopy(PLAN)
    adversarial["tests"].append(
        {
            "id": len(adversarial["tests"]) + 1,
            "category": "adversarial",
            "description": "Problem-specific adversarial case",
        }
    )
    assert GeneratorPlan.model_validate(adversarial).tests[-1].category == "adversarial"


def test_state_lock_and_checkpoint(cfg):
    state = StateStore(cfg.state_file)
    state.update(PID.key, "statement_ready")
    state.update(PID.key, "failed", last_error="fixture")
    assert (
        StateStore(cfg.state_file).entry(PID.key)["last_completed_step"]
        == "statement_ready"
    )
    with (
        importer_lock(cfg.state_file.with_suffix(".lock")),
        pytest.raises(RuntimeError, match="Another importer"),
        importer_lock(cfg.state_file.with_suffix(".lock")),
    ):
        pass


class FastSandbox:
    calls = 0

    def __init__(self, cfg, directory):
        self.directory = directory

    def build(self, candidates, statement, spec, plan):
        type(self).calls += 1
        tests = self.directory / "tests"
        entries = []
        for item in plan.tests:
            text, output = f"1\n1\n{item.id}\n", f"{item.id}\n"
            name = f"{item.id:02d}"
            write_text(tests / (name + ".in"), text)
            write_text(tests / (name + ".out"), output)
            entries.append(
                {
                    "name": name,
                    "input_sha256": digest(text),
                    "output_sha256": digest(output),
                }
            )
        return candidates[0], {
            "tests": entries,
            "source_path": str(candidates[0]),
            "source_sha256": digest(candidates[0].read_bytes()),
        }


class APITransport:
    def __init__(self, client, monkeypatch, *, verdict="AC", lose_test_response=False):
        from minioj.database import SessionLocal
        from minioj.models import ApiToken, User
        from minioj.security import create_api_token, hash_password

        self.client, self.verdict = client, verdict
        self.lose_test_response = lose_test_response
        self.submissions = 0
        self.mutations = 0
        token_id, token, hashed, expires = create_api_token()
        with SessionLocal() as db:
            user = User(
                username="fixture",
                email="fixture@example.test",
                password_hash=hash_password("fixture-password"),
                role="system",
            )
            db.add(user)
            db.flush()
            db.add(
                ApiToken(
                    id=token_id,
                    user_id=user.id,
                    name="fixture",
                    token_hash=hashed,
                    expires_at=expires,
                )
            )
            db.commit()
        monkeypatch.setenv("CF_IMPORT_MINIOJ_TOKEN", token)

    def request(self, method, url, *, payload=None, token="", **kwargs):
        from minioj.database import SessionLocal
        from minioj.models import Submission

        path = url.removeprefix("http://testserver")
        response = self.client.request(
            method, path, json=payload, headers={"Authorization": "Bearer " + token}
        )
        if response.status_code >= 400:
            raise HTTPError(response.status_code, "fixture HTTP failure")
        value = response.json() if response.content else None
        if method != "GET":
            self.mutations += 1
        if method == "POST" and path.endswith("/submissions"):
            self.submissions += 1
            with SessionLocal() as db:
                submission = db.get(Submission, value["submission_id"])
                if self.verdict is not None:
                    submission.status, submission.verdict = "FINISHED", self.verdict
                db.commit()
        if self.lose_test_response and method == "POST" and path.endswith("/testcases"):
            self.lose_test_response = False
            raise RuntimeError("Lost upload response after server commit")
        return value


def importer(cfg, *, remote=None, sandbox=FastSandbox):
    upstream = UpstreamHTTP()
    return Importer(
        cfg,
        cf=CodeforcesClient(cfg, upstream),
        statements=StatementProvider(cfg, upstream),
        llm=LLMClient(cfg.llm, upstream),
        minioj=remote,
        sandbox_factory=sandbox,
    )


def test_dry_run_never_contacts_minioj_and_reuses_artifacts(cfg):
    class Forbidden:
        def ensure_admin(self):
            pytest.fail("dry-run contacted MiniOJ")

    FastSandbox.calls = 0
    first = importer(cfg, remote=Forbidden()).run(dry_run=True)
    second = importer(cfg, remote=Forbidden()).run(dry_run=True)
    assert first["dry_run_success"] == second["dry_run_success"] == 1
    assert FastSandbox.calls == 1
    assert StateStore(cfg.state_file).entry(PID.key)["status"] == "tests_ready"


@pytest.mark.parametrize("explicit_override", [False, True])
def test_cached_good_reference_is_reselected_when_primary_becomes_preferred(
    cfg, explicit_override
):
    primary = cfg.source_dir / str(PID.contest_id) / "A.cpp"
    good = primary.with_name("A__Good.cpp")
    good.write_bytes(primary.read_bytes())
    if explicit_override:
        cfg.references[PID.external_id] = str(good)
    else:
        primary.unlink()
    FastSandbox.calls = 0
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
    manifest_path = (
        cfg.generated_dir / str(PID.contest_id) / PID.index / "tests_manifest.json"
    )
    assert read_json(manifest_path)["source_path"] == str(good)

    if explicit_override:
        cfg.references.clear()
    else:
        primary.write_bytes(good.read_bytes())
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
    assert read_json(manifest_path)["source_path"] == str(primary)
    assert FastSandbox.calls == 2
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
    assert FastSandbox.calls == 2


def test_legacy_manifest_cannot_keep_good_reference(cfg):
    from store.cf_import.pipeline import dependencies

    primary = cfg.source_dir / str(PID.contest_id) / "A.cpp"
    good = primary.with_name("A__Good.cpp")
    good.write_bytes(primary.read_bytes())
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
    directory = cfg.generated_dir / str(PID.contest_id) / PID.index
    manifest_path = directory / "tests_manifest.json"
    manifest = read_json(manifest_path)
    manifest["source_path"] = str(good)
    manifest["dependencies"] = dependencies(directory, cfg, good, [good, primary])
    manifest.pop("reference_selection_policy")
    write_json(manifest_path, manifest)
    FastSandbox.calls = 0
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
    assert read_json(manifest_path)["source_path"] == str(primary)
    assert FastSandbox.calls == 1


def test_import_verify_skip_force_and_lost_response(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch, lose_test_response=True)
    remote = MiniOJClient(cfg.minioj, api)
    first = importer(cfg, remote=remote).run()
    assert first["failed"] == 1
    result = importer(cfg, remote=remote).run()
    assert result["imported"] == 1 and api.submissions == 1
    assert len(remote.testcases(PID.minioj_id)) == 13
    mutations = api.mutations
    assert importer(cfg, remote=remote).run()["already_imported"] == 1
    assert api.mutations == mutations
    assert importer(cfg, remote=remote).run(force=True)["imported"] == 1
    assert len(remote.testcases(PID.minioj_id)) == 13 and api.submissions == 2
    from minioj.database import SessionLocal
    from minioj.models import Problem

    with SessionLocal() as db:
        problem = db.get(Problem, PID.minioj_id)
        assert problem.checker == "tokens" and problem.source_id == PID.external_id


def test_non_ac_judge_is_not_reported_as_success(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch, verdict="WA")
    result = importer(cfg, remote=MiniOJClient(cfg.minioj, api)).run()
    assert result["imported"] == 0 and result["minioj_validation_failed"] == 1
    assert (
        StateStore(cfg.state_file).entry(PID.key)["status"]
        == "minioj_validation_failed"
    )


def test_generator_failure_blocks_upload_and_stays_bounded(cfg):
    class Broken(FastSandbox):
        calls = 0

        def build(self, *args):
            type(self).calls += 1
            raise GenerationError("invalid permutation")

    assert importer(cfg, sandbox=Broken).run(dry_run=True)["failed"] == 1
    assert importer(cfg, sandbox=Broken).run(dry_run=True)["failed"] == 1
    assert Broken.calls == 1


def test_not_ac_does_not_fetch_statement_or_call_llm(cfg):
    upstream = UpstreamHTTP()
    client = CodeforcesClient(cfg, upstream)
    accepted, info = client.sync_ac()
    assert accepted
    # Cache an empty complete history for the same account.
    path = next(cfg.cache_dir.rglob("accepted.json"))
    write_json(path, dict(info, accepted=[]))
    result = importer(cfg).run(dry_run=True)
    assert result["skipped_not_ac"] == 1 and result["selected"] == 0
    assert not cfg.generated_dir.exists()


def test_http_errors_do_not_echo_credentials(monkeypatch):
    import urllib.error
    import urllib.request

    class Broken:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError(
                "https://example.test?key=SECRET", 403, "SECRET", {}, None
            )

    client = HTTPClient()
    client.opener = Broken()
    with pytest.raises(HTTPError) as error:
        client.request("GET", "https://example.test?key=SECRET", token="SECRET")
    assert "SECRET" not in str(error.value)


def test_testcase_inventory_requires_admin(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch)
    remote = MiniOJClient(cfg.minioj, api)
    importer(cfg, remote=remote).run()
    path = f"/api/v1/admin/problems/{PID.minioj_id}/testcases"
    assert client.get(path).status_code == 401
    rows = remote.testcases(PID.minioj_id)
    assert all(
        set(row)
        == {"id", "type", "order", "input_sha256", "output_sha256", "created_at"}
        for row in rows
    )
    assert all("input" not in row and "output" not in row for row in rows)


def test_legacy_update_preserves_existing_checker(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch)
    remote = MiniOJClient(cfg.minioj, api)
    importer(cfg, remote=remote).run()
    payload = {"id": PID.minioj_id, "title": "Edited", "statement": "Edited"}
    detail = remote.request("PUT", f"/admin/problems/{PID.minioj_id}", payload)
    assert detail["checker"] == "tokens"
    detail = remote.request(
        "PUT", f"/admin/problems/{PID.minioj_id}", dict(payload, checker="lines")
    )
    assert detail["checker"] == "lines"


def test_modified_tests_never_upload(cfg):
    importer(cfg).run(dry_run=True)
    test = cfg.generated_dir / str(PID.contest_id) / PID.index / "tests/01.in"
    test.write_text("1\n1\n101\n")
    result = importer(cfg).run(dry_run=True)
    assert result["failed"] == 1
    assert "modified" in StateStore(cfg.state_file).entry(PID.key)["last_error"]


def test_interactive_problem_never_generates_or_uploads(cfg):
    tool = importer(cfg)
    tool.run(dry_run=True)
    path = cfg.generated_dir / str(PID.contest_id) / PID.index / "problem_spec.json"
    data = read_json(path)
    data["output_mode"] = "interactive"
    write_json(path, data)
    assert importer(cfg).run(dry_run=True)["failed"] == 1
    assert (
        "Unsupported interactive"
        in StateStore(cfg.state_file).entry(PID.key)["last_error"]
    )


def test_checker_upload_is_atomic_private_and_preserves_legacy_edits(
    cfg, client, monkeypatch
):
    from minioj.database import SessionLocal
    from minioj.judge.testlib import CheckerBundle
    from minioj.models import Problem

    api = APITransport(client, monkeypatch)
    remote = MiniOJClient(cfg.minioj, api)
    source = (FIXTURES / "checker.cpp").read_text()
    payload = {
        "id": "SPJ101",
        "title": "Constructive",
        "statement": "Print any pair",
        "checker": "testlib",
        "checker_source": source,
    }
    remote.ensure_problem(payload, None)
    meta = remote.request("GET", "/admin/problems/SPJ101/checker")
    assert meta == {
        "checker": "testlib",
        "name": "checker.cpp",
        "sha256": digest(
            CheckerBundle("checker.cpp", {"checker.cpp": source}).serialize()
        ),
    }
    assert "checker_source" not in remote.request("GET", "/problems/SPJ101")
    assert "checker_bundle" not in remote.request("GET", "/agent/problems/SPJ101")
    with SessionLocal() as db:
        problem = db.get(Problem, "SPJ101")
        assert (
            CheckerBundle.load(problem.checker_bundle, problem.checker_sha256).files[
                "checker.cpp"
            ]
            == source
        )
        revision = problem.revision
    remote.request("PUT", "/admin/problems/SPJ101", payload)
    with SessionLocal() as db:
        assert db.get(Problem, "SPJ101").revision == revision
    legacy = {"id": "SPJ101", "title": "Updated", "statement": "Print any pair"}
    remote.request("PUT", "/admin/problems/SPJ101", legacy)
    assert remote.request("GET", "/admin/problems/SPJ101/checker") == meta
    remote.request("PUT", "/admin/problems/SPJ101", dict(legacy, checker="tokens"))
    assert remote.request("GET", "/admin/problems/SPJ101/checker") == {
        "checker": "tokens",
        "name": None,
        "sha256": None,
    }
    with SessionLocal() as db:
        assert db.get(Problem, "SPJ101").checker_bundle is None


def test_checker_api_rejects_incomplete_or_oversized_sources(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch)
    remote = MiniOJClient(cfg.minioj, api)
    base = {"id": "SPJ102", "title": "Test", "statement": "Test"}
    for fields in (
        {"checker": "testlib"},
        {"checker": "tokens", "checker_source": "int main(){return 0;}"},
        {"checker": "testlib", "checker_source": "int main(){return 0;}\x00"},
        {"checker": "testlib", "checker_source": "//" + "汉" * 100000},
    ):
        with pytest.raises(HTTPError) as error:
            remote.request("POST", "/admin/problems", dict(base, **fields))
        assert error.value.status == 422
    assert remote.request("GET", "/problems") == []


def test_checker_metadata_requires_admin(cfg, client, monkeypatch):
    from minioj.database import SessionLocal
    from minioj.models import User

    api = APITransport(client, monkeypatch)
    remote = MiniOJClient(cfg.minioj, api)
    with SessionLocal() as db:
        user = db.query(User).filter_by(username="fixture").one()
        user.role = "user"
        db.commit()
    with pytest.raises(HTTPError) as error:
        remote.request("GET", "/admin/problems/nonexistent/checker")
    assert error.value.status == 403


def test_non_unique_examples_require_alternative_valid_answers(cfg):
    from scripts.cf_import_fixture import CHECKER_EXAMPLES
    from store.cf_import.checkers import CheckerHarness
    from store.cf_import.generation import CheckerError

    directory = cfg.generated_dir / "test"
    examples = copy.deepcopy(CHECKER_EXAMPLES)
    for case in examples["cases"]:
        case["valid_outputs"] = case["valid_outputs"][:1]
    write_json(directory / "checker_tests.json", examples)

    class Unavailable:
        def compile(self, *args):
            pytest.fail("Alternatives must be required before compiling")

    class Sandbox:
        judge = Unavailable()

    sandbox = Sandbox()
    sandbox.directory = directory
    with pytest.raises(CheckerError, match="different valid answers"):
        CheckerHarness(
            sandbox,
            directory / "work",
            ProblemSpec.model_validate(dict(SPEC, output_mode="non_unique")),
        )


def test_checker_failures_block_upload_and_keep_separate_repair_budget(cfg):
    from scripts.cf_import_fixture import SPECIAL_PID, constructive_reference
    from store.cf_import.generation import CheckerError

    cfg.llm.repair_attempts = 1
    source = cfg.source_dir / str(SPECIAL_PID.contest_id) / "B.cpp"
    source.write_text(constructive_reference())
    upstream = UpstreamHTTP(constructive=True)

    class Broken(FastSandbox):
        calls = 0

        def build(self, *args):
            type(self).calls += 1
            raise CheckerError("checker accepted an invalid output")

    def tool():
        return Importer(
            cfg,
            cf=CodeforcesClient(cfg, upstream),
            statements=StatementProvider(cfg, upstream),
            llm=LLMClient(cfg.llm, upstream),
            sandbox_factory=Broken,
        )

    assert tool().run(dry_run=True)["failed"] == 1
    assert Broken.calls == 2
    entry = StateStore(cfg.state_file).entry(SPECIAL_PID.key)
    assert entry["checker_exhausted"] and entry["checker_repair_count"] == 1
    assert entry["repair_count"] == 0
    assert tool().run(dry_run=True)["failed"] == 1
    assert Broken.calls == 2
    assert (
        "Checker repair budget exhausted"
        in StateStore(cfg.state_file).entry(SPECIAL_PID.key)["last_error"]
    )


def test_verification_timeout_resumes_same_submission(cfg, client, monkeypatch):
    api = APITransport(client, monkeypatch, verdict=None)

    class Pending(MiniOJClient):
        first = True

        def verify(self, submission_id):
            from minioj.database import SessionLocal
            from minioj.models import Submission

            if self.first:
                self.first = False
                raise TimeoutError("Fixture worker is still running")
            with SessionLocal() as db:
                submission = db.get(Submission, submission_id)
                submission.status, submission.verdict = "FINISHED", "AC"
                db.commit()
            return super().verify(submission_id)

    remote = Pending(cfg.minioj, api)
    assert importer(cfg, remote=remote).run()["failed"] == 1
    previous = StateStore(cfg.state_file).entry(PID.key)["submission_id"]
    assert importer(cfg, remote=remote).run()["imported"] == 1
    assert api.submissions == 1
    assert StateStore(cfg.state_file).entry(PID.key)["submission_id"] == previous


def test_windows_reference_source_checksum(cfg):
    source = cfg.source_dir / str(PID.contest_id) / "A.cpp"
    source.write_bytes(source.read_bytes().replace(b"\n", b"\r\n"))
    assert importer(cfg).run(dry_run=True)["dry_run_success"] == 1
