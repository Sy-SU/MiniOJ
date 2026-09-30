from __future__ import annotations

import tempfile
from pathlib import Path

from minioj.config import settings
from minioj.judge import DockerJudge

STANDARD_SOURCE = r"""
#include <iostream>
int main() {
    long long left, right;
    if (!(std::cin >> left >> right)) return 1;
    std::cout << left + right << '\n';
}
"""

GENERATOR_SOURCE = r"""
#include <iostream>
int main(int argc, char **argv) {
    if (argc != 3) return 2;
    std::cout << argv[1] << ' ' << argv[2] << '\n';
}
"""


def main() -> None:
    original_job_dir = settings.job_dir
    with tempfile.TemporaryDirectory(prefix="minioj-generator-smoke-") as temporary:
        job_dir = Path(temporary) / "jobs"
        job_dir.mkdir()
        object.__setattr__(settings, "job_dir", job_dir)
        try:
            cases = DockerJudge().build_testcases(
                STANDARD_SOURCE,
                generator_source=GENERATOR_SOURCE,
                case_count=2,
                base_seed=20,
            )
        finally:
            object.__setattr__(settings, "job_dir", original_job_dir)
    expected = [("20 1\n", "21\n"), ("21 2\n", "23\n")]
    if cases != expected:
        raise SystemExit(f"Unexpected generated cases: {cases!r}")
    print("Generator smoke passed: 2 inputs and outputs built in Docker.")


if __name__ == "__main__":
    main()
