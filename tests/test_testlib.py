from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from test_api import add_user, auth
from test_judge import process
from test_polygon import XML, admin_session, package
from test_worker import _user_and_problem

from minioj import polygon
from minioj.config import settings
from minioj.database import SessionLocal
from minioj.judge import runner
from minioj.judge.runner import DockerJudge
from minioj.judge.testlib import (
    BUNDLE_LIMIT_BYTES,
    FILE_LIMIT_BYTES,
    STANDARD_CHECKERS,
    VENDOR_DIR,
    CheckerBundle,
)
from minioj.models import Problem, Submission
from minioj.polygon import parse_polygon
from minioj.problems import add_testcase
from minioj.worker.main import judge_submission

CUSTOM_XML = XML.replace(
    '<checker name="std::wcmp.cpp"/>',
    '<checker name="my-check.cpp" type="testlib"><source path="files/check.cpp" type="cpp.g++17"/></checker>',
)
CUSTOM_SOURCE = '#include "testlib.h"\n// PRIVATE_CHECKER_SOURCE\nint main() {}'


def bundle_from(parsed):
    return CheckerBundle.load(
        parsed.values["checker_bundle"], parsed.values["checker_sha256"]
    )


@pytest.mark.parametrize("name", sorted(STANDARD_CHECKERS))
def test_all_vendored_standard_checkers_can_be_imported(name):
    parsed = parse_polygon(package(xml=XML.replace("std::wcmp.cpp", "std::" + name)))
    bundle = bundle_from(parsed)
    assert parsed.values["checker"] == "testlib"
    assert (
        bundle.files[bundle.entrypoint] == (VENDOR_DIR / "checkers" / name).read_text()
    )
    assert bundle.files["testlib.h"] == (VENDOR_DIR / "testlib.h").read_text()


def test_custom_source_and_local_nested_headers_are_preserved():
    bundle = bundle_from(
        parse_polygon(
            package(
                xml=CUSTOM_XML,
                extra={
                    "files/check.cpp": CUSTOM_SOURCE.encode(),
                    "files/testlib.h": b"// package header",
                    "files/lib/helper.hpp": b"// PRIVATE_CHECKER_HEADER",
                },
                prefix="outer/",
            )
        )
    )
    assert bundle.entrypoint == "files/check.cpp"
    assert bundle.files["files/check.cpp"] == CUSTOM_SOURCE
    assert bundle.files["files/testlib.h"] == "// package header"
    assert bundle.files["files/lib/helper.hpp"] == "// PRIVATE_CHECKER_HEADER"


def test_packaged_source_takes_precedence_even_for_known_checker_name():
    parsed = parse_polygon(
        package(
            xml=CUSTOM_XML.replace("my-check.cpp", "std::ncmp.cpp"),
            extra={"files/check.cpp": CUSTOM_SOURCE.encode()},
        )
    )
    assert bundle_from(parsed).files["files/check.cpp"] == CUSTOM_SOURCE


@pytest.mark.parametrize(
    "xml, extra, error",
    [
        (CUSTOM_XML, {}, "missing"),
        (CUSTOM_XML.replace('type="cpp.g++17"', 'type="exe"'), {}, "C\\+\\+"),
        (CUSTOM_XML.replace("files/check.cpp", "../check.cpp"), {}, "Unsafe"),
        (CUSTOM_XML.replace('type="testlib"', 'type="python"'), {}, "C\\+\\+ testlib"),
        (XML.replace("std::wcmp.cpp", "std::pointscmp.cpp"), {}, "Scoring"),
        (XML.replace("std::wcmp.cpp", "std::unknown.cpp"), {}, "export"),
        (CUSTOM_XML, {"files/check.cpp": b"\x00"}, "NUL"),
        (CUSTOM_XML, {"files/check.cpp": b"\xff"}, "UTF-8"),
        (
            CUSTOM_XML,
            {
                "files/check.cpp": CUSTOM_SOURCE.encode(),
                "files/oversize.h": b"x" * (FILE_LIMIT_BYTES + 1),
            },
            "size limit",
        ),
    ],
)
def test_invalid_checkers_are_rejected_at_import(xml, extra, error):
    with pytest.raises(ValueError, match=error):
        parse_polygon(package(xml=xml, extra=extra))


@pytest.mark.parametrize(
    "entrypoint,files",
    [
        ("../a.cpp", {"../a.cpp": "source"}),
        (".", {".": "source"}),
        ("/a.cpp", {"/a.cpp": "source"}),
        ("a.cpp", {"a.cpp": "source", "main": "conflict"}),
        ("a.cpp", {"a.cpp": "source", "_minioj_case/input": "conflict"}),
        ("a.cpp", {"a.cpp": "source", "dir": "x", "dir/extra.h": "x"}),
        ("missing.cpp", {"a.cpp": "source"}),
        ("a.cpp", {"a.cpp": "source", **{f"h{i}.h": "x" for i in range(128)}}),
        (
            "a.cpp",
            {
                "a.cpp": "source",
                **{
                    f"h{i}.h": "x" * FILE_LIMIT_BYTES
                    for i in range(BUNDLE_LIMIT_BYTES // FILE_LIMIT_BYTES)
                },
            },
        ),
    ],
)
def test_bundle_paths_and_aggregate_limits_are_validated(entrypoint, files):
    with pytest.raises(ValueError):
        CheckerBundle(entrypoint, files).serialize()


def test_bundle_integrity_is_checked_before_use():
    encoded = CheckerBundle("checker.cpp", {"checker.cpp": "int main(){}"}).serialize()
    with pytest.raises(ValueError, match="checksum"):
        CheckerBundle.load(encoded, "0" * 64)
    for invalid in ("[]", '{"entrypoint": "x"}', "null"):
        with pytest.raises(ValueError, match="Invalid"):
            CheckerBundle.load(invalid, hashlib.sha256(invalid.encode()).hexdigest())


def test_import_rejects_aggregate_limit_before_reading_excess_resources(monkeypatch):
    original_text = polygon._Archive.text
    reads = []

    def text(self, path, limit):
        reads.append(path)
        return original_text(self, path, limit)

    monkeypatch.setattr(polygon._Archive, "text", text)
    extra = {"files/check.cpp": CUSTOM_SOURCE.encode()}
    extra.update({f"files/header{i}.h": b"x" * FILE_LIMIT_BYTES for i in range(5)})
    with pytest.raises(ValueError, match="4 MiB"):
        parse_polygon(package(xml=CUSTOM_XML, extra=extra))
    assert "files/header3.h" not in reads


def checker_judge(
    monkeypatch, tmp_path, checked, *, checker_compile=None, execution=None
):
    local_settings = replace(settings, job_dir=tmp_path)
    monkeypatch.setattr(runner, "settings", local_settings)
    judge = DockerJudge("test-image")
    monkeypatch.setattr(judge, "ensure_available", lambda: None)
    bundle = CheckerBundle(
        "files/check.cpp", {"files/check.cpp": "checker", "files/helper.h": "header"}
    )
    compilation_calls = []
    execution_calls = []

    def compile(directory, source, memory, *, source_path="main.cpp"):
        compilation_calls.append((directory, source, memory, source_path))
        if source == "checker":
            assert (directory / "files/helper.h").read_text() == "header"
            return checker_compile or process(stdout="")
        return process(stdout="")

    def execute(directory, stdin, time, memory, *, arguments=None):
        execution_calls.append((directory, stdin, time, memory, arguments))
        if arguments is None:
            assert not (directory / "_minioj_case").exists()
            return execution or process(
                stdout="alternative valid answer", time_ms=9, memory_kb=123
            )
        assert stdin == ""
        assert time == local_settings.checker_time_limit_ms
        assert memory == local_settings.checker_memory_mb
        assert arguments == [
            "/work/_minioj_case/" + f for f in ("input", "output", "answer")
        ]
        assert (directory / "_minioj_case/input").read_text() == "input"
        assert (
            directory / "_minioj_case/output"
        ).read_text() == "alternative valid answer"
        assert (directory / "_minioj_case/answer").read_text() == "answer"
        assert directory != execution_calls[0][0]
        return checked

    monkeypatch.setattr(judge, "compile", compile)
    monkeypatch.setattr(judge, "execute", execute)
    result = judge.judge(
        "contestant",
        [("input", "answer")],
        1000,
        64,
        checker="testlib",
        checker_bundle=bundle,
    )
    assert list(tmp_path.iterdir()) == []
    assert len(compilation_calls) == 2
    return result, execution_calls


@pytest.mark.parametrize(
    "code, expected",
    [
        (0, "AC"),
        (1, "WA"),
        (2, "WA"),
        (4, "WA"),
        (8, "WA"),
        (3, "IE"),
        (7, "IE"),
        (16, "IE"),
        (139, "IE"),
    ],
)
def test_default_testlib_exit_codes_and_contestant_resources(
    monkeypatch, tmp_path, code, expected
):
    (compiled, result), _ = checker_judge(
        monkeypatch,
        tmp_path,
        process(
            exit_code=code,
            stderr="PRIVATE_CHECKER_DIAGNOSTIC",
            time_ms=999,
            memory_kb=999999,
        ),
    )
    assert compiled["success"]
    assert result["verdict"] == expected
    assert result["resources"] == {"time_ms": 9, "memory_kb": 123}
    assert "PRIVATE_CHECKER_DIAGNOSTIC" not in json.dumps(result)
    if expected == "IE":
        assert result["test_results"][0]["verdict"] == "IE"
        assert "failure" not in result


@pytest.mark.parametrize("failure", ["timed_out", "oom_killed", "output_exceeded"])
def test_checker_limit_failures_are_ie_not_contestant_failures(
    monkeypatch, tmp_path, failure
):
    (_, result), _ = checker_judge(monkeypatch, tmp_path, process(**{failure: True}))
    assert result["verdict"] == "IE"


def test_checker_compile_failure_is_ie_not_ce(monkeypatch, tmp_path):
    (compiled, result), executions = checker_judge(
        monkeypatch,
        tmp_path,
        process(),
        checker_compile=process(exit_code=1, stderr="PRIVATE_COMPILER_DIAGNOSTIC"),
    )
    assert compiled["success"]
    assert result["verdict"] == "IE"
    assert executions == []
    assert "PRIVATE_COMPILER_DIAGNOSTIC" not in json.dumps(result)


@pytest.mark.parametrize(
    "execution, verdict",
    [
        (process(exit_code=5), "RE"),
        (process(timed_out=True), "TLE"),
        (process(stdout_valid_utf8=False), "WA"),
    ],
)
def test_checker_is_not_run_after_contestant_failure(
    monkeypatch, tmp_path, execution, verdict
):
    (_, result), executions = checker_judge(
        monkeypatch, tmp_path, process(), execution=execution
    )
    assert result["verdict"] == verdict
    assert len(executions) == 1


def test_checker_source_is_not_exposed_to_users(client):
    csrf = admin_session(client)
    response = client.post(
        "/admin/problems/import",
        data={"csrf_token": csrf},
        files={
            "package_file": (
                "custom.zip",
                package(
                    xml=CUSTOM_XML,
                    extra={
                        "files/check.cpp": CUSTOM_SOURCE.encode(),
                        "files/helper.h": b"// PRIVATE_HEADER",
                    },
                ),
            )
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    client.post("/logout", data={"csrf_token": csrf})
    _, token = add_user("checker-reader")
    for path in (
        "/problems/polygon-sum",
        "/api/v1/problems/polygon-sum",
        "/api/v1/agent/problems/polygon-sum",
    ):
        response = client.get(path, headers=auth(token))
        assert response.status_code == 200
        assert "PRIVATE_CHECKER_SOURCE" not in response.text
        assert "PRIVATE_HEADER" not in response.text


@pytest.mark.parametrize("corrupt", [False, True])
def test_worker_snapshots_checker_and_rejects_corruption(monkeypatch, corrupt):
    user_id, problem_id = _user_and_problem()
    encoded = CheckerBundle("check.cpp", {"check.cpp": "checker snapshot"}).serialize()
    with SessionLocal() as db:
        problem = db.get(Problem, problem_id)
        problem.checker = "testlib"
        problem.checker_bundle = encoded
        problem.checker_sha256 = (
            "0" * 64 if corrupt else hashlib.sha256(encoded.encode()).hexdigest()
        )
        db.commit()
        add_testcase(db, problem, "hidden", "", "1")
        submission = Submission(
            user_id=user_id,
            problem_id=problem_id,
            source_code="int main(){}",
            status="COMPILING",
        )
        db.add(submission)
        db.commit()
        submission_id = submission.id
    calls = []

    def judge(*args, **kwargs):
        calls.append(kwargs)
        assert kwargs["checker"] == "testlib"
        assert kwargs["checker_bundle"].files["check.cpp"] == "checker snapshot"
        kwargs["on_compiled"]()
        from minioj.judge.contracts import compile_result_payload

        return compile_result_payload(process(stdout="")), {
            "verdict": "AC",
            "summary": "Accepted.",
        }

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", judge)
    judge_submission(submission_id)
    with SessionLocal() as db:
        row = db.get(Submission, submission_id)
        assert row.verdict == ("IE" if corrupt else "AC")
        assert len(calls) == (0 if corrupt else 1)
        assert "checksum" not in row.judge_result
