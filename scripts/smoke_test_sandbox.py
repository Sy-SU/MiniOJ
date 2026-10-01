from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from minioj.judge import DockerJudge


@dataclass(frozen=True)
class SandboxCase:
    name: str
    source: str
    expected: str
    time_ms: int = 3000


CASES = [
    SandboxCase(
        "network",
        r"""
#include <arpa/inet.h>
#include <iostream>
#include <sys/socket.h>
#include <unistd.h>
int main() {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in target{};
    target.sin_family = AF_INET;
    target.sin_port = htons(53);
    inet_pton(AF_INET, "1.1.1.1", &target.sin_addr);
    int connected = connect(fd, reinterpret_cast<sockaddr*>(&target), sizeof(target));
    if (fd >= 0) close(fd);
    std::cout << (connected == -1 ? "network-blocked\n" : "network-open\n");
}
""",
        "network-blocked\n",
    ),
    SandboxCase(
        "host-files",
        r"""
#include <filesystem>
#include <fstream>
#include <iostream>
int main() {
    const char* forbidden[] = {
        "/var/run/docker.sock", "/app/database/oj.db", "/work/.env", "/root/.ssh"
    };
    for (const char* path : forbidden) {
        std::error_code error;
        if (std::filesystem::exists(path, error) && !error) {
            std::cout << "exposed:" << path << '\n';
            return 0;
        }
    }
    std::ofstream root_file("/minioj-write-test");
    std::cout << (!root_file ? "host-isolated\n" : "root-writable\n");
}
""",
        "host-isolated\n",
    ),
    SandboxCase(
        "pid-limit",
        r"""
#include <csignal>
#include <iostream>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
int main() {
    std::vector<pid_t> children;
    bool limited = false;
    for (int i = 0; i < 100; ++i) {
        pid_t child = fork();
        if (child < 0) { limited = true; break; }
        if (child == 0) { pause(); _exit(0); }
        children.push_back(child);
    }
    for (pid_t child : children) kill(child, SIGKILL);
    for (pid_t child : children) waitpid(child, nullptr, 0);
    std::cout << (limited ? "pid-limited\n" : "pid-unlimited\n");
}
""",
        "pid-limited\n",
        time_ms=5000,
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
    owner = f"sandbox-smoke-{os.getpid()}"
    judge = DockerJudge(owner=owner)
    judge.ensure_available()
    if _owned_containers(owner):
        raise SystemExit("Sandbox smoke owner unexpectedly has existing containers.")
    failures: list[str] = []
    for case in CASES:
        _, result = judge.judge(
            case.source,
            [("", case.expected)],
            case.time_ms,
            128,
        )
        print(f"{case.name:12} verdict={result['verdict']}")
        if result["verdict"] != "AC":
            failures.append(f"{case.name}: {result['summary']}")
    leftovers = _owned_containers(owner)
    if leftovers:
        failures.append(f"leftover containers: {', '.join(leftovers)}")
        judge.cleanup_owned_containers()
    if failures:
        raise SystemExit("Sandbox smoke failed:\n" + "\n".join(failures))
    print("Sandbox network, host-file, PID, read-only-root, and cleanup checks passed.")


if __name__ == "__main__":
    main()
