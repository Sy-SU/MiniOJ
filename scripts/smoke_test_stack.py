from __future__ import annotations

import os
import tempfile
from pathlib import Path

SOURCE = """#include <iostream>
int main() { long long a, b; if (std::cin >> a >> b) std::cout << a + b << '\\n'; }
"""


def require_status(response, expected: int, operation: str) -> dict:
    if response.status_code != expected:
        raise RuntimeError(
            f"{operation} returned HTTP {response.status_code}: {response.text}"
        )
    return response.json()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="minioj-stack-smoke-") as temp_dir:
        root = Path(temp_dir)
        os.environ["MINIOJ_DATABASE_URL"] = f"sqlite:///{root / 'smoke.db'}"
        os.environ["MINIOJ_DATA_DIR"] = str(root / "data")
        os.environ["MINIOJ_JOB_DIR"] = str(root / "jobs")
        os.environ["MINIOJ_SECRET_KEY"] = "isolated-stack-smoke-test-secret"

        from fastapi.testclient import TestClient

        from minioj.database import SessionLocal, init_db
        from minioj.models import ApiToken, Problem, User
        from minioj.problems import add_testcase
        from minioj.security import create_api_token, hash_password
        from minioj.server.main import app
        from minioj.worker.main import run_worker

        init_db()
        token_id, raw_token, token_hash, expires_at = create_api_token()
        with SessionLocal() as db:
            user = User(
                username="smoke-user",
                email="smoke@example.com",
                password_hash=hash_password("smoke-test-password"),
                role="user",
            )
            db.add(user)
            db.flush()
            db.add(
                ApiToken(
                    id=token_id,
                    user_id=user.id,
                    name="stack-smoke-test",
                    token_hash=token_hash,
                    expires_at=expires_at,
                )
            )
            problem = Problem(
                id="smoke-sum",
                title="Smoke Test Sum",
                statement="Read two integers and print their sum.",
                input_specification="Two integers.",
                output_specification="Their sum.",
                time_limit_ms=1000,
                memory_limit_mb=128,
                created_by=user.id,
            )
            db.add(problem)
            db.commit()
            add_testcase(db, problem, "hidden", "20 22\n", "42\n")

        headers = {"Authorization": f"Bearer {raw_token}"}
        with TestClient(app) as client:
            run_result = require_status(
                client.post(
                    "/api/v1/runs",
                    headers=headers,
                    json={"code": SOURCE, "language": "cpp20", "stdin": "7 8\n"},
                ),
                200,
                "custom run",
            )
            if run_result["status"] != "OK" or run_result["stdout"] != "15\n":
                raise RuntimeError(f"Unexpected custom-run result: {run_result}")

            queued = require_status(
                client.post(
                    "/api/v1/submissions",
                    headers=headers,
                    json={
                        "problem_id": "smoke-sum",
                        "language": "cpp20",
                        "source_code": SOURCE,
                    },
                ),
                202,
                "submission creation",
            )
            submission_id = queued["submission_id"]
            run_worker(once=True)

            submission = require_status(
                client.get(f"/api/v1/submissions/{submission_id}", headers=headers),
                200,
                "submission lookup",
            )
            feedback = require_status(
                client.get(
                    f"/api/v1/agent/submissions/{submission_id}/feedback",
                    headers=headers,
                ),
                200,
                "agent feedback lookup",
            )

        if submission["status"] != "FINISHED" or submission["verdict"] != "AC":
            raise RuntimeError(f"Unexpected submission result: {submission}")
        if feedback["verdict"] != "AC":
            raise RuntimeError(f"Unexpected agent feedback: {feedback}")
        if feedback["resources"]["memory_kb"] <= 0:
            raise RuntimeError(f"Memory usage was not measured: {feedback}")

        print(
            "Full stack passed: custom run OK; submission AC; "
            f"time_ms={feedback['resources']['time_ms']}; "
            f"memory_kb={feedback['resources']['memory_kb']}."
        )


if __name__ == "__main__":
    main()
