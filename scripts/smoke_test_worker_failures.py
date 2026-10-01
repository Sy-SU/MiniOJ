from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="minioj-worker-faults-") as temporary:
        root = Path(temporary)
        os.environ["MINIOJ_DATABASE_URL"] = f"sqlite:///{root / 'oj.db'}"
        os.environ["MINIOJ_DATA_DIR"] = str(root / "data")
        os.environ["MINIOJ_JOB_DIR"] = str(root / "jobs")
        os.environ["MINIOJ_SECRET_KEY"] = "worker-fault-smoke-secret-key-32-chars"

        from fastapi.testclient import TestClient

        from minioj.config import settings
        from minioj.database import SessionLocal, init_db
        from minioj.models import Problem, Submission, User
        from minioj.problems import add_testcase
        from minioj.security import hash_password
        from minioj.server.main import app
        from minioj.worker.main import claim_next_submission, judge_submission

        init_db()
        with SessionLocal() as db:
            user = User(
                username="FaultAdmin",
                email="fault-admin@example.com",
                password_hash=hash_password("fault-smoke-password"),
                role="admin",
            )
            db.add(user)
            db.flush()
            corrupt_problem = Problem(
                id="corrupt-case",
                title="Corrupt testcase",
                statement="Fault injection.",
                created_by=user.id,
            )
            docker_problem = Problem(
                id="docker-case",
                title="Docker failure",
                statement="Fault injection.",
                created_by=user.id,
            )
            missing_problem = Problem(
                id="missing-case",
                title="Missing testcase",
                statement="Fault injection.",
                created_by=user.id,
            )
            db.add_all([corrupt_problem, missing_problem, docker_problem])
            db.commit()
            corrupt_testcase = add_testcase(db, corrupt_problem, "hidden", "1\n", "1\n")
            missing_testcase = add_testcase(db, missing_problem, "hidden", "1\n", "1\n")
            add_testcase(db, docker_problem, "hidden", "1\n", "1\n")
            db.add_all(
                [
                    Submission(
                        user_id=user.id,
                        problem_id=corrupt_problem.id,
                        source_code="int main(){}",
                    ),
                    Submission(
                        user_id=user.id,
                        problem_id=missing_problem.id,
                        source_code="int main(){}",
                    ),
                    Submission(
                        user_id=user.id,
                        problem_id=docker_problem.id,
                        source_code="int main(){}",
                    ),
                ]
            )
            db.commit()
            corrupt_path = settings.data_dir / corrupt_testcase.input_path
            missing_path = settings.data_dir / missing_testcase.output_path

        corrupt_path.write_text("changed\n", encoding="utf-8")
        missing_path.unlink()
        corrupt_id = claim_next_submission()
        if corrupt_id is None:
            raise SystemExit("Could not claim corrupt-testcase submission.")
        judge_submission(corrupt_id)

        missing_id = claim_next_submission()
        if missing_id is None:
            raise SystemExit("Could not claim missing-testcase submission.")
        judge_submission(missing_id)

        original_image = settings.docker_image
        object.__setattr__(
            settings, "docker_image", "minioj-phase2-missing-image:never"
        )
        try:
            docker_id = claim_next_submission()
            if docker_id is None:
                raise SystemExit("Could not claim Docker-failure submission.")
            judge_submission(docker_id)
        finally:
            object.__setattr__(settings, "docker_image", original_image)

        with SessionLocal() as db:
            corrupt = db.get(Submission, corrupt_id)
            missing = db.get(Submission, missing_id)
            docker = db.get(Submission, docker_id)
            corrupt_result = json.loads(corrupt.judge_result)
            missing_result = json.loads(missing.judge_result)
            docker_result = json.loads(docker.judge_result)
            if (corrupt.status, corrupt.verdict) != ("FINISHED", "IE"):
                raise SystemExit("Corrupt testcase did not finish as IE.")
            if (missing.status, missing.verdict) != ("FINISHED", "IE"):
                raise SystemExit("Missing testcase did not finish as IE.")
            if (docker.status, docker.verdict) != ("FINISHED", "IE"):
                raise SystemExit("Docker failure did not finish as IE.")
            if "checksum" in corrupt_result["summary"].lower():
                raise SystemExit("Corrupt testcase detail leaked into public summary.")
            if "no such file" in missing_result["summary"].lower():
                raise SystemExit("Filesystem diagnostic leaked into public summary.")
            if "missing-image" in docker_result["summary"].lower():
                raise SystemExit("Docker diagnostic leaked into public summary.")

        with TestClient(app) as client:
            health = client.get("/healthz")
            if health.status_code != 200:
                raise SystemExit("Web health check failed after worker faults.")
        print(
            "Worker fault smoke passed: corrupt/missing testcases and unavailable "
            "image became safe IE results; Web health remained 200."
        )


if __name__ == "__main__":
    main()
