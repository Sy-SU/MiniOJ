from __future__ import annotations

import json

from minioj.database import SessionLocal
from minioj.judge import InfrastructureError
from minioj.models import Problem, Submission, User
from minioj.problems import add_testcase
from minioj.worker.main import judge_submission


def test_worker_maps_judge_infrastructure_failure_to_ie(monkeypatch):
    with SessionLocal() as db:
        user = User(
            username="worker_user",
            email="worker@example.com",
            password_hash="not-used-by-this-test",
        )
        db.add(user)
        db.flush()
        problem = Problem(
            id="worker-problem",
            title="Worker Problem",
            statement="Print one.",
            created_by=user.id,
        )
        db.add(problem)
        db.commit()
        add_testcase(db, problem, "hidden", "", "1\n")
        submission = Submission(
            id="sub_worker_ie",
            user_id=user.id,
            problem_id=problem.id,
            language="cpp20",
            source_code="int main() {}",
            status="COMPILING",
        )
        db.add(submission)
        db.commit()

    def fail_judge(*_args, **_kwargs):
        raise InfrastructureError("daemon unavailable")

    monkeypatch.setattr("minioj.worker.main.DockerJudge.judge", fail_judge)
    judge_submission("sub_worker_ie")

    with SessionLocal() as db:
        submission = db.get(Submission, "sub_worker_ie")
        result = json.loads(submission.judge_result)
        assert submission.status == "FINISHED"
        assert submission.verdict == "IE"
        assert result["verdict"] == "IE"
        assert "daemon unavailable" in result["summary"]
