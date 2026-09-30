from __future__ import annotations

from dataclasses import dataclass

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
]


def main() -> None:
    judge = DockerJudge()
    failures: list[str] = []
    for case in CASES:
        _, result = judge.judge(
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
    if failures:
        raise SystemExit("Judge smoke test failed:\n" + "\n".join(failures))
    print("All Docker judge verdicts passed.")


if __name__ == "__main__":
    main()
