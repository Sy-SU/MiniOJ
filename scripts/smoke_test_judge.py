from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from minioj.config import settings
from minioj.judge import DockerJudge


@dataclass(frozen=True)
class Case:
    name: str
    expected: str
    source: str
    stdin: str = "2 3\n"
    stdout: str = "5\n"
    time_ms: int = 1000
    memory_mb: int = 128


CASES = [
    Case(
        "accepted",
        "AC",
        """#include <iostream>
int main() { long long a, b; if (std::cin >> a >> b) std::cout << a + b << '\\n'; }
""",
    ),
    Case(
        "wrong-answer",
        "WA",
        """#include <iostream>
int main() { std::cout << 0 << '\\n'; }
""",
    ),
    Case("compile-error", "CE", "int main( { return 0; }\n"),
    Case(
        "runtime-error",
        "RE",
        """int main() { int* pointer = nullptr; *pointer = 1; }
""",
    ),
    Case(
        "time-limit",
        "TLE",
        """int main() { for (;;) { asm volatile(\"\"); } }
""",
        time_ms=200,
    ),
    Case(
        "memory-limit",
        "MLE",
        """#include <cstdlib>
int main() {
    constexpr unsigned long size = 256UL * 1024 * 1024;
    auto* memory = static_cast<volatile char*>(std::malloc(size));
    if (!memory) return 1;
    for (unsigned long i = 0; i < size; i += 4096) memory[i] = 1;
}
""",
        memory_mb=32,
    ),
    Case(
        "output-limit",
        "OLE",
        """#include <iostream>
int main() { for (;;) std::cout << \"0123456789abcdef\\n\"; }
        """,
    ),
    Case("user-exit-124", "RE", "int main() { return 124; }\n"),
    Case("user-exit-137", "RE", "int main() { return 137; }\n"),
    Case(
        "short-output",
        "OLE",
        """#include <iostream>
#include <string>
int main() { std::cout << std::string(OUTPUT_LIMIT_BYTES + 1, 'x'); }
""".replace("OUTPUT_LIMIT_BYTES", str(settings.output_limit_bytes)),
    ),
]


def _owned_containers(owner: str) -> list[str]:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            f"label=minioj.owner={owner}",
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return result.stdout.split()


def main() -> None:
    owner = f"judge-smoke-{os.getpid()}"
    judge = DockerJudge(owner=owner)
    judge.ensure_available()
    before_jobs = {path.name for path in settings.jobs_dir.glob("judge-*")}
    failures: list[str] = []
    for case in CASES:
        compile_result, result = judge.judge(
            case.source,
            [(case.stdin, case.stdout)],
            case.time_ms,
            case.memory_mb,
        )
        verdict = result["verdict"]
        resources = result.get("resources", {})
        print(
            f"{case.name:15} expected={case.expected:3} actual={verdict:3} "
            f"time_ms={resources.get('time_ms', 0)} "
            f"memory_kb={resources.get('memory_kb', 0)}"
        )
        if verdict != case.expected:
            failures.append(
                f"{case.name}: expected {case.expected}, received {verdict}"
            )
        required_compile_fields = {
            "success",
            "exit_code",
            "stdout",
            "stderr",
            "time_ms",
            "memory_kb",
            "timed_out",
            "output_exceeded",
            "oom_killed",
            "stdout_truncated",
            "stderr_truncated",
            "output_truncated",
        }
        if set(compile_result) != required_compile_fields:
            failures.append(f"{case.name}: incomplete compile result")
        if verdict != "CE" and not result.get("test_results"):
            failures.append(f"{case.name}: missing per-test result metadata")
    original_output_limit = settings.output_limit_bytes
    object.__setattr__(settings, "output_limit_bytes", 512)
    try:
        compile_result, result = judge.judge(
            '#error "' + ("x" * 5000) + '"\n',
            [("", "")],
            1000,
            128,
        )
    finally:
        object.__setattr__(settings, "output_limit_bytes", original_output_limit)
    print(
        "compile-output  "
        f"expected=CE  actual={result['verdict']:3} "
        f"truncated={compile_result['output_truncated']}"
    )
    if result["verdict"] != "CE":
        failures.append("compile-output: expected CE")
    if not compile_result["output_exceeded"]:
        failures.append("compile-output: output limit was not detected")
    if not compile_result["output_truncated"]:
        failures.append("compile-output: truncation was not recorded")
    leftovers = _owned_containers(owner)
    if leftovers:
        failures.append(f"leftover containers: {', '.join(leftovers)}")
        judge.cleanup_owned_containers()
    after_jobs = {path.name for path in settings.jobs_dir.glob("judge-*")}
    if after_jobs != before_jobs:
        failures.append(
            "leftover job directories: " + ", ".join(sorted(after_jobs - before_jobs))
        )
    if failures:
        raise SystemExit("Judge smoke test failed:\n" + "\n".join(failures))
    print("All Docker judge verdicts and exit-code boundaries passed.")


if __name__ == "__main__":
    main()
