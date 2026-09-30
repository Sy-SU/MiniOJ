from __future__ import annotations

import json
import re

from minioj.database import SessionLocal
from minioj.models import Problem, Sample, Submission, User


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_authenticated_web_pages_render_with_real_data(client):
    register_page = client.get("/register")
    csrf = csrf_from(register_page.text)
    response = client.post(
        "/register",
        data={
            "csrf_token": csrf,
            "username": "webadmin",
            "email": "web@example.com",
            "password": "long-enough-password",
            "password_confirmation": "long-enough-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with SessionLocal() as db:
        user = db.query(User).filter_by(username="webadmin").one()
        user.role = "admin"
        problem = Problem(
            id="web-problem",
            title="Web Problem",
            statement="Print one.",
            input_specification="Nothing.",
            output_specification="One.",
            notes="A note.",
            tags="smoke, web",
            rating=900,
            created_by=user.id,
        )
        db.add(problem)
        db.flush()
        db.add(Sample(problem_id=problem.id, input="", output="1\n", order=1))
        submission = Submission(
            id="sub_web_smoke",
            user_id=user.id,
            problem_id=problem.id,
            language="cpp20",
            source_code="int main() { return 0; }",
            status="FINISHED",
            verdict="AC",
            compile_result=json.dumps({"success": True, "stderr": ""}),
            judge_result=json.dumps(
                {
                    "verdict": "AC",
                    "summary": "Accepted. Passed 1 test.",
                    "tests": {"total": 1, "passed": 1, "failed_test": None},
                    "resources": {"time_ms": 4, "memory_kb": 0},
                }
            ),
        )
        db.add(submission)
        db.commit()

    paths = [
        "/",
        "/problems",
        "/problems/web-problem",
        "/submissions",
        "/submissions/sub_web_smoke",
        "/settings",
        "/admin",
        "/admin/problems/new",
        "/admin/problems/web-problem/edit",
    ]
    for path in paths:
        page = client.get(path)
        assert page.status_code == 200, path
        assert "MiniOJ" in page.text
