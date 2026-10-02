"""Real Docker testlib checks; isolated jobs only, no database/deployment writes."""

from __future__ import annotations

import argparse
import io
import os
import subprocess
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from minioj.config import settings
from minioj.judge import runner
from minioj.judge.contracts import Verdict
from minioj.judge.runner import DockerJudge
from minioj.judge.testlib import STANDARD_CHECKERS, VENDOR_DIR, CheckerBundle
from minioj.polygon import parse_polygon


def standard_bundle(name: str) -> CheckerBundle:
    return CheckerBundle(
        "checker.cpp",
        {
            "checker.cpp": (VENDOR_DIR / "checkers" / name).read_text(),
            "testlib.h": (VENDOR_DIR / "testlib.h").read_text(),
        },
    )


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--polygon-package", type=Path)
    args = parser.parse_args()
    owner = f"checker-smoke-{os.getpid()}"
    judge = DockerJudge(owner=owner)
    judge.ensure_available()
    with tempfile.TemporaryDirectory(prefix="minioj-checker-smoke-") as temporary:
        root = Path(temporary)
        runner.settings = replace(
            settings, job_dir=root, checker_time_limit_ms=200, checker_memory_mb=128
        )
        try:
            for name in sorted(STANDARD_CHECKERS):
                directory = root / name
                directory.mkdir()
                if not judge._compile_checker(directory, standard_bundle(name)):
                    raise AssertionError(f"{name} failed to compile")
                if name.startswith("case"):
                    expected, good, bad = "Case 1: 42\n", "Case 1: 42\n", "Case 1: 43\n"
                elif name in {"yesno.cpp", "nyesno.cpp"}:
                    expected, good, bad = "YES\n", "yes\n", "NO\n"
                elif name == "uncmp.cpp":
                    expected, good, bad = "1 2 3\n", "3 1 2\n", "3 1 1\n"
                elif name in {
                    "rcmp4.cpp",
                    "rcmp6.cpp",
                    "rcmp9.cpp",
                    "acmp.cpp",
                    "dcmp.cpp",
                    "rcmp.cpp",
                    "rncmp.cpp",
                }:
                    expected, good, bad = "1.0\n", "1.0000000001\n", "2.0\n"
                elif name == "lcmp.cpp":
                    expected, good, bad = "1 2\n3\n", "1   2\n3\n", "1\n2 3\n"
                else:
                    expected, good, bad = "42\n", "42\n", "43\n"
                assert (
                    judge._check_output(directory, "", good, expected) == Verdict.AC
                ), name
                assert (
                    judge._check_output(directory, "", bad, expected) == Verdict.WA
                ), name
                judge._remove_job_directory(directory)
                print(f"{name}: Docker compilation / AC / WA passed", flush=True)

            template = """#include "testlib.h"
#include "lib/rule.hpp"
#include <fstream>
#include <unistd.h>
int main(int argc, char** argv) {
 registerTestlibCmd(argc, argv);
 if (getuid() != 1000 || std::ifstream("/work/PRIVATE_CONTESTANT_FILE").good())
   quitf(_fail, "sandbox isolation failed");
 int n = inf.readInt(), got = ouf.readInt();
 if (rule(n, got)) quitf(_ok, "alternative answer accepted");
 quitf(_wa, "invalid answer");
}"""
            custom = CheckerBundle(
                "files/check.cpp",
                {
                    "files/check.cpp": template,
                    "files/testlib.h": (VENDOR_DIR / "testlib.h").read_text(),
                    "files/lib/rule.hpp": "bool rule(int n, int got) { return got*got == n; }",
                },
            )
            original_compile = judge.compile

            def compile_with_marker(directory, source, memory, **kwargs):
                result = original_compile(directory, source, memory, **kwargs)
                if not kwargs:
                    (directory / "PRIVATE_CONTESTANT_FILE").write_text(
                        "private contestant job"
                    )
                return result

            judge.compile = compile_with_marker
            for output, verdict in (("-2", "AC"), ("3", "WA")):
                source = (
                    "#include <iostream>\n#include <unistd.h>\nint main(){"
                    'if(access("/work/_minioj_case/answer", F_OK)==0) return 17;'
                    'std::cout << "' + output + '";}'
                )
                compiled, result = judge.judge(
                    source,
                    [("4", "2")],
                    1000,
                    64,
                    checker="testlib",
                    checker_bundle=custom,
                )
                assert compiled["success"] and result["verdict"] == verdict, result
                assert result["resources"]["time_ms"] < 100, result
                print(
                    f"custom C++ checker: alternative-answer {verdict}, contestant CPU {result['resources']['time_ms']} ms",
                    flush=True,
                )
            judge.compile = original_compile
            broken = CheckerBundle("checker.cpp", {"checker.cpp": "int main("})
            compiled, result = judge.judge(
                "int main(){}",
                [("", "")],
                1000,
                64,
                checker="testlib",
                checker_bundle=broken,
            )
            assert compiled["success"] and result["verdict"] == "IE", result
            print(
                "checker compilation failure: safe IE, not contestant CE, passed",
                flush=True,
            )

            directory = root / "failure-checker"
            directory.mkdir()
            failing = CheckerBundle(
                "check.cpp",
                {
                    "check.cpp": '#include "testlib.h"\nint main(int argc,char**argv){registerTestlibCmd(argc,argv);quitf(_fail,"private error");}',
                    "testlib.h": (VENDOR_DIR / "testlib.h").read_text(),
                },
            )
            assert judge._compile_checker(directory, failing)
            assert judge._check_output(directory, "", "", "") == Verdict.IE
            judge._remove_job_directory(directory)
            directory.mkdir()
            hanging = CheckerBundle(
                "check.cpp", {"check.cpp": 'int main(){for(;;){asm volatile("");}}'}
            )
            assert judge._compile_checker(directory, hanging)
            assert judge._check_output(directory, "", "", "") == Verdict.IE
            judge._remove_job_directory(directory)
            print("checker failure / CPU timeout: IE passed", flush=True)

            # Exercise the actual Polygon parser and supplied-source/header snapshot.
            xml = """<problem short-name="checker-smoke"><names><name language="english" value="Checker smoke"/></names>
<statements><statement language="english" type="text/html" path="statement.html"/></statements>
<judging input-file="" output-file=""><testset name="tests"><time-limit>1000</time-limit><memory-limit>67108864</memory-limit>
<test-count>1</test-count><input-path-pattern>tests/%d</input-path-pattern><answer-path-pattern>tests/%d.a</answer-path-pattern>
<tests><test method="manual" sample="true"/></tests></testset></judging>
<assets><checker name="square.cpp" type="testlib"><source path="files/check.cpp" type="cpp.g++17"/></checker></assets></problem>"""
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                for path, value in {
                    "problem.xml": xml,
                    "statement.html": '<div class="legend">Print a square root.</div>',
                    "tests/1": "4",
                    "tests/1.a": "2",
                    **custom.files,
                }.items():
                    archive.writestr(path, value)
            parsed = parse_polygon(stream.getvalue())
            compiled, result = judge.judge(
                "#include <iostream>\nint main(){std::cout << -2;}",
                [(i.decode(), o.decode()) for i, o in parsed.cases],
                1000,
                64,
                checker="testlib",
                checker_bundle=CheckerBundle.load(
                    parsed.values["checker_bundle"], parsed.values["checker_sha256"]
                ),
            )
            assert result["verdict"] == "AC", result
            print(
                "Polygon ZIP -> custom checker -> alternative answer AC passed",
                flush=True,
            )

            if args.polygon_package:
                parsed = parse_polygon(args.polygon_package.read_bytes())
                assert parsed.values["standard_source"], (
                    "Package needs a main C++ solution"
                )
                compiled, result = judge.judge(
                    parsed.values["standard_source"],
                    [(i.decode(), o.decode()) for i, o in parsed.cases],
                    parsed.values["time_limit_ms"],
                    parsed.values["memory_limit_mb"],
                    checker="testlib",
                    checker_bundle=CheckerBundle.load(
                        parsed.values["checker_bundle"], parsed.values["checker_sha256"]
                    ),
                )
                assert compiled["success"] and result["verdict"] == "AC", result
                print(
                    f"Actual package {args.polygon_package.name}: {parsed.values['checker_name']}, {len(parsed.cases)} tests AC",
                    flush=True,
                )
            assert list(root.iterdir()) == [], (
                "Leaked checker/contestant job directories"
            )
        finally:
            judge.cleanup_owned_containers()
            listed = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"label=minioj.owner={owner}"],
                capture_output=True,
                text=True,
                check=True,
            )
            assert not listed.stdout.strip(), "Leaked checker containers"
    print(
        "All checker smoke tests passed; isolated jobs/containers cleaned.", flush=True
    )


if __name__ == "__main__":
    run()
