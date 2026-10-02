from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

SOURCE = """#include <iostream>
int main() { long long a, b; if (std::cin >> a >> b) std::cout << a + b << '\\n'; }
"""


@dataclass(frozen=True)
class Result:
    status: int
    body: bytes
    headers: object

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> object:
        return json.loads(self.body)


class MiniOJClient:
    """Deliberately standalone: this client only knows HTTP, Bearer, and JSON."""

    def __init__(self, base_url: str, token: str) -> None:
        self.api_base = base_url.rstrip("/") + "/api/v1"
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict | None = None,
        *,
        token: str | None = None,
    ) -> Result:
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Authorization": f"Bearer {token or self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.api_base + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except urllib.error.HTTPError as exc:
            return Result(exc.code, exc.read(), exc.headers)
        with response:
            return Result(response.status, response.read(), response.headers)


def expect(result: Result, status: int, operation: str) -> object:
    if result.status != status:
        raise RuntimeError(
            f"{operation} returned HTTP {result.status}: {result.text[:2000]}"
        )
    return result.json() if result.body else None


def run_flow(base_url: str, token: str, problem_id: str, timeout: float) -> int:
    client = MiniOJClient(base_url, token)
    me = expect(client.request("GET", "/me"), 200, "identity")
    if not isinstance(me, dict) or not me.get("username"):
        raise RuntimeError(f"Unexpected identity response: {me}")

    problems = expect(client.request("GET", "/problems"), 200, "problem list")
    if not isinstance(problems, list) or problem_id not in {
        problem.get("problem_id") for problem in problems
    }:
        raise RuntimeError(f"Problem {problem_id!r} is missing from the list")
    expect(client.request("GET", f"/problems/{problem_id}"), 200, "problem detail")
    agent_problem = expect(
        client.request("GET", f"/agent/problems/{problem_id}"),
        200,
        "agent problem detail",
    )
    if (
        not isinstance(agent_problem, dict)
        or {
            "rating",
            "tags",
            "testcases",
        }
        & agent_problem.keys()
    ):
        raise RuntimeError(f"Agent problem contains excluded fields: {agent_problem}")

    token_created = expect(
        client.request(
            "POST",
            "/tokens",
            {"name": "phase4-independent-smoke", "expires_in_days": 1},
        ),
        201,
        "token creation",
    )
    if not isinstance(token_created, dict):
        raise TypeError(f"Unexpected token response: {token_created}")
    child_id = token_created["id"]
    child_secret = token_created["token"]
    listed = expect(client.request("GET", "/tokens"), 200, "token list")
    if child_secret in json.dumps(listed) or not any(
        item.get("id") == child_id for item in listed
    ):
        raise RuntimeError("Token metadata list leaked or omitted the created token")
    expect(client.request("DELETE", f"/tokens/{child_id}"), 204, "token revoke")
    invalid = client.request("GET", "/me", token=child_secret)
    invalid_body = expect(invalid, 401, "revoked token rejection")
    if invalid_body["error"]["code"] != "authentication_required":
        raise RuntimeError(f"Unexpected revoked-token error: {invalid_body}")
    listed = expect(client.request("GET", "/tokens"), 200, "revoked token metadata")
    child = next(item for item in listed if item.get("id") == child_id)
    if child["revoked_at"] is None:
        raise RuntimeError("Revoked token metadata was not retained")

    custom = expect(
        client.request(
            "POST",
            "/runs",
            {"language": "cpp20", "source_code": SOURCE, "stdin": "7 8\n"},
        ),
        200,
        "Custom Run",
    )
    if custom["status"] != "OK" or custom["stdout"] != "15\n":
        raise RuntimeError(f"Unexpected Custom Run response: {custom}")

    queued = expect(
        client.request(
            "POST",
            "/submissions",
            {
                "problem_id": problem_id,
                "language": "cpp20",
                "source_code": SOURCE,
            },
        ),
        202,
        "submission creation",
    )
    if set(queued) != {"submission_id", "status"} or queued["status"] != "QUEUED":
        raise RuntimeError(f"Unexpected submission creation response: {queued}")
    submission_id = queued["submission_id"]
    deadline = time.monotonic() + timeout
    while True:
        submission = expect(
            client.request("GET", f"/submissions/{submission_id}"),
            200,
            "submission lookup",
        )
        if submission["status"] == "FINISHED":
            break
        if any(
            submission[field] is not None
            for field in ("verdict", "tests", "resources", "finished_at")
        ):
            raise RuntimeError(f"Pending fields must be null: {submission}")
        if time.monotonic() >= deadline:
            raise RuntimeError("Submission polling timed out")
        time.sleep(0.2)
    if submission["verdict"] != "AC":
        raise RuntimeError(f"Submission was not accepted: {submission}")
    resources = submission["resources"]
    if not isinstance(resources["time_ms"], int) or resources["time_ms"] < 0:
        raise RuntimeError(f"Invalid millisecond resource value: {resources}")
    if resources["memory_kb"] is not None and resources["memory_kb"] < 0:
        raise RuntimeError(f"Invalid KiB resource value: {resources}")
    return submission_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exercise MiniOJ only through its public HTTP/Bearer/JSON API."
    )
    parser.add_argument("--base-url", default=os.getenv("OJ_BASE_URL"))
    parser.add_argument("--token", default=os.getenv("OJ_API_TOKEN"))
    parser.add_argument(
        "--problem-id", default=os.getenv("OJ_PROBLEM_ID", "phase3-sum")
    )
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    if not args.base_url or not args.token:
        parser.error("--base-url/OJ_BASE_URL and --token/OJ_API_TOKEN are required")
    submission_id = run_flow(args.base_url, args.token, args.problem_id, args.timeout)
    print(
        "Phase 4 independent API smoke passed: identity, problems, token revoke, "
        f"Custom Run, submission #{submission_id}, polling, and AC."
    )


if __name__ == "__main__":
    main()
