from __future__ import annotations

import json
from pathlib import Path

import pytest
from test_phase1 import login, problem_payload
from test_problem_lifecycle import setup_history

from minioj.database import SessionLocal
from minioj.models import Problem, Submission
from minioj.problems import delete_problem, update_problem
from minioj.server.main import app


@pytest.fixture
def problem_history(client):
    _, _, pid, sid = setup_history(client)
    login(client, "HistoryAdmin")
    with SessionLocal() as db:
        submission = db.get(Submission, sid)
        submission.status = "FINISHED"
        submission.verdict = "AC"
        db.commit()
        update_problem(db, db.get(Problem, pid), problem_payload(pid))
    return pid, sid


@pytest.fixture(scope="module")
def chromium():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        if not Path(runtime.chromium.executable_path).is_file():
            pytest.skip("Install Chromium with python -m playwright install chromium")
        browser = runtime.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def browser_context(chromium, client):
    context = chromium.new_context()
    errors = []
    context.on(
        "page", lambda page: page.on("pageerror", lambda error: errors.append(error))
    )

    def serve(route):
        # Render real application pages/assets without a deployed server or Worker.
        # Resolve redirects in the isolated client, never through testserver DNS.
        response = client.get(route.request.url, follow_redirects=True)
        route.fulfill(
            status=response.status_code,
            body=response.content,
            content_type=response.headers.get("content-type", "text/plain"),
        )

    context.route("**/*", serve)
    yield context
    context.close()
    assert not errors


@pytest.mark.parametrize("deleted", [False, True], ids=["edited", "deleted"])
@pytest.mark.parametrize("path", ["/submissions", "/admin", "/submissions/{sid}"])
def test_history_pages_do_not_show_problem_lifecycle_warnings(
    client, problem_history, deleted, path
):
    pid, sid = problem_history
    if deleted:
        with SessionLocal() as db:
            delete_problem(db, db.get(Problem, pid))
    response = client.get(path.format(sid=sid))
    assert response.status_code == 200
    assert "This problem has been modified" not in response.text
    assert "This problem has been deleted" not in response.text
    assert "data-dismiss-key" not in response.text


@pytest.mark.parametrize("prefix", ["", "/minioj"])
def test_legacy_admin_redirect_preserves_prefix(
    client, problem_history, monkeypatch, prefix
):
    monkeypatch.setattr(app, "root_path", prefix)
    response = client.get(f"{prefix}/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"{prefix}/manage"
    destination = client.get(response.headers["location"])
    assert destination.status_code == 200
    assert "Dashboard" in destination.text


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("deleted", [False, True], ids=["missing", "deleted"])
def test_unavailable_problem_has_a_plain_not_found_page(
    client, problem_history, monkeypatch, prefix, deleted
):
    pid, _ = problem_history
    if deleted:
        with SessionLocal() as db:
            delete_problem(db, db.get(Problem, pid))
    else:
        pid = "never-existed"
    monkeypatch.setattr(app, "root_path", prefix)
    response = client.get(f"{prefix}/problems/{pid}")
    assert response.status_code == (410 if deleted else 404)
    assert response.headers["content-type"].startswith("text/html")
    assert "Problem not found" in response.text
    assert "The requested problem does not exist." in response.text
    assert f'href="{prefix}/problems"' in response.text
    assert "Updated statement" not in response.text
    assert "code-editor" not in response.text
    assert "alert-warning" not in response.text


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("deleted", [False, True], ids=["edited", "deleted"])
def test_browser_history_is_warning_free_and_problem_link_shows_unavailability(
    browser_context, problem_history, monkeypatch, prefix, deleted
):
    pid, sid = problem_history
    if deleted:
        with SessionLocal() as db:
            delete_problem(db, db.get(Problem, pid))
    monkeypatch.setattr(app, "root_path", prefix)
    page = browser_context.new_page()
    for path in (f"/submissions/{sid}", "/admin", "/submissions"):
        page.goto(f"http://testserver{prefix}{path}")
        assert page.locator(".alert-warning").count() == 0
        assert page.locator("[data-dismiss-key]").count() == 0
    problem_url = f"http://testserver{prefix}/problems/{pid}"
    with page.expect_response(problem_url) as navigation:
        page.locator(f'a[href="{prefix}/problems/{pid}"]').click()
    assert navigation.value.status == (410 if deleted else 200)
    assert page.locator(".alert-warning").count() == 0
    if deleted:
        assert page.locator("h1").inner_text() == "Problem not found"
        assert (
            "The requested problem does not exist." in page.locator("main").inner_text()
        )
        assert page.locator("#code-editor").count() == 0
    else:
        assert page.locator("#code-editor").count() == 1
    page.reload()
    assert page.locator(".alert-warning").count() == 0


@pytest.mark.parametrize("keyboard", [False, True], ids=["mouse", "keyboard"])
def test_other_warning_buttons_still_work(browser_context, problem_history, keyboard):
    _, sid = problem_history
    with SessionLocal() as db:
        submission = db.get(Submission, sid)
        submission.verdict = "CE"
        submission.judge_result = json.dumps(
            {"verdict": "CE", "summary": "Compilation failed."}
        )
        submission.compile_result = json.dumps(
            {"success": False, "stderr": "Compile error", "output_truncated": True}
        )
        db.commit()
    page = browser_context.new_page()
    page.goto(f"http://testserver/submissions/{sid}")
    button = page.get_by_role("button", name="Dismiss warning")
    assert button.count() == 1
    assert "Compile output was truncated" in page.locator(".alert-warning").inner_text()
    if keyboard:
        button.focus()
        button.press("Enter")
    else:
        button.click()
    assert page.locator(".alert-warning").count() == 0
    page.reload()
    assert page.locator(".alert-warning").count() == 1
