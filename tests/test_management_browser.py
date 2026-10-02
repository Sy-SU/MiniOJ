from __future__ import annotations

import socket
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
import test_management
import test_problem_pages
import uvicorn
from test_management import create_contest, make_submission, png_bytes
from test_phase1 import create_problem, login

from minioj.database import SessionLocal
from minioj.models import Contest
from minioj.server.main import app

chromium = test_problem_pages.chromium
feature_data = test_management.feature_data


@pytest.fixture
def browser(chromium, client, request):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", access_log=False, log_level="warning")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("Isolated browser server did not start")
        time.sleep(0.01)
    context = chromium.new_context(base_url=address, **getattr(request, "param", {}))
    context._minioj_address = address
    errors = []
    context.on(
        "page",
        lambda page: page.on("pageerror", lambda error: errors.append(str(error))),
    )

    try:
        yield context
    finally:
        context.close()
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive()
    assert not errors


def browser_login(context, client, name):
    login(client, name)
    context.clear_cookies()
    context.add_cookies(
        [
            {
                "name": "minioj_session",
                "value": client.cookies.get("minioj_session"),
                "url": context._minioj_address,
            }
        ]
    )


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("width", [1365, 390])
@pytest.mark.parametrize(
    "role,name",
    [("user", "Contestant"), ("admin", "ContentMgr"), ("system", "SysOwner")],
)
def test_console_navigation_layout_and_permissions(
    browser, client, feature_data, monkeypatch, prefix, width, role, name, tmp_path
):
    monkeypatch.setattr(app, "root_path", prefix)
    browser_login(browser, client, name)
    page = browser.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{prefix}/problems")
    entry = page.locator(".management-entry")
    assert entry.count() == int(role != "user")
    if role == "user":
        response = page.goto(f"{prefix}/manage")
        assert response.status == 403
        return
    assert entry.evaluate("node => getComputedStyle(node).color") == "rgb(198, 40, 40)"
    entry.click()
    assert page.locator(".console-sidebar").is_visible()
    assert (
        page.locator(".console-sidebar").evaluate(
            "node => getComputedStyle(node).backgroundColor"
        )
        == "rgb(23, 35, 58)"
    )
    assert page.get_by_role("heading", name="Dashboard", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.locator('.console-sidebar a[href$="/manage/users"]').count() == int(
        role == "system"
    )
    page.locator('.console-sidebar a[href$="/manage/problems"]').click()
    assert page.get_by_role("heading", name="Problem management").is_visible()
    page.get_by_role("link", name="Edit / Testcases").click()
    assert page.locator("#standard-solution").is_visible()
    assert page.locator(".console-sidebar").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if width == 1365 and role == "system" and not prefix:
        page.screenshot(path=str(tmp_path / "management-editor.png"), full_page=True)


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("name", ["ContentMgr", "SysOwner"])
@pytest.mark.parametrize("state", ["UPCOMING", "RUNNING"])
@pytest.mark.parametrize(
    "browser",
    [{}, {"java_script_enabled": False}],
    indirect=True,
    ids=["js", "no-js"],
)
def test_browser_contest_form_preserves_newlines_on_repeated_save(
    browser, client, feature_data, monkeypatch, prefix, name, state
):
    monkeypatch.setattr(app, "root_path", prefix)
    create_problem(feature_data["admin"][0], "feature-two")
    create_problem(feature_data["admin"][0], "feature-three")
    browser_login(browser, client, name)
    page = browser.new_page()
    page.goto(f"{prefix}/manage/contests/new")
    start = datetime.now(UTC).replace(microsecond=123000) + timedelta(
        hours=1 if state == "UPCOMING" else -1
    )
    end = start + timedelta(hours=3)
    description = "First line\n\n第二段；保留字面量 \\n 和 <tags> & symbols"
    ids = ["feature-one", "feature-two", "feature-three"]
    page.locator('input[name="title"]').fill("Multiline round")
    page.locator('textarea[name="description"]').fill(description)
    page.locator('input[name="start_time"]').fill(
        start.replace(tzinfo=None).isoformat(timespec="milliseconds")
    )
    page.locator('input[name="end_time"]').fill(
        end.replace(tzinfo=None).isoformat(timespec="milliseconds")
    )
    problem_ids = page.locator('textarea[name="problem_ids"]')
    enhanced = page.locator("#contest-problem-controls").is_visible()
    if enhanced:
        for problem_id in ids:
            page.locator("#contest-add-id").fill(problem_id)
            page.get_by_role("button", name="Add problem", exact=True).click()
    else:
        problem_ids.fill("\n".join(ids))
    for round_number in range(3):
        if round_number == 2:
            page.locator('input[name="title"]').fill("Updated multiline round")
            if state == "UPCOMING":
                ids.reverse()
                if enhanced:
                    for problem_id in reversed(ids[:-1]):
                        button = page.get_by_role(
                            "button", name=f"Move up {problem_id}", exact=True
                        )
                        while button.is_enabled():
                            button.click()
                else:
                    problem_ids.fill("\n".join(ids))
        with (
            page.expect_navigation(wait_until="domcontentloaded"),
            page.expect_response(
                lambda response: response.request.method == "POST"
            ) as mutation,
        ):
            page.get_by_role("button", name="Save contest", exact=True).click()
        assert mutation.value.status == 303, mutation.value.text()
        assert page.url.endswith("/edit")
        assert problem_ids.input_value() == "\n".join(ids)
        assert page.locator('textarea[name="description"]').input_value() == description
        cid = int(page.url.rsplit("/", 2)[1])
        with SessionLocal() as db:
            contest = db.get(Contest, cid)
            assert [row.problem_id for row in contest.problems] == ids
            assert contest.description.replace("\r\n", "\n") == description
            assert contest.start_time.replace(tzinfo=UTC) == start
            assert contest.end_time.replace(tzinfo=UTC) == end
            if round_number == 2:
                assert contest.title == "Updated multiline round"
            if round_number == 0:
                # Existing service-created contests may have submillisecond times.
                start = start.replace(microsecond=123456)
                end = end.replace(microsecond=654321)
                contest.start_time, contest.end_time = start, end
                db.commit()
        if round_number == 0:
            page.reload(wait_until="domcontentloaded")
        for field, value in [("start_time", start), ("end_time", end)]:
            assert page.locator(f'input[name="{field}"]').input_value() == (
                value.replace(tzinfo=None).isoformat(timespec="milliseconds")
            )


@pytest.mark.parametrize("prefix", ["", "/minioj"])
def test_browser_rejudge_confirmation_and_avatar_upload(
    browser, client, feature_data, monkeypatch, prefix
):
    monkeypatch.setattr(app, "root_path", prefix)
    sid = make_submission(feature_data, verdict="AC")
    browser_login(browser, client, "ContentMgr")
    page = browser.new_page()
    page.goto(f"{prefix}/manage/submissions/{sid}")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.get_by_role("button", name="Rejudge", exact=True).click()
    assert page.locator("#submission-verdict").inner_text() == "AC"
    page.once("dialog", lambda dialog: dialog.accept())
    with page.expect_response(
        lambda response: response.request.method == "POST"
    ) as mutation:
        page.get_by_role("button", name="Rejudge", exact=True).click()
    assert mutation.value.status == 303, mutation.value.text()
    page.wait_for_url(f"**{prefix}/manage/submissions/{sid}")
    page.wait_for_function(
        "document.querySelector('#submission-verdict')?.textContent === 'QUEUED'"
    )
    assert page.get_by_role("heading", name="Judge History").is_visible()
    assert page.get_by_role("button", name="Rejudge", exact=True).count() == 0
    browser_login(browser, client, "Contestant")
    page.goto(f"{prefix}/settings")
    page.locator('input[name="avatar"]').set_input_files(
        {"name": "avatar.png", "mimeType": "image/png", "buffer": png_bytes()}
    )
    page.get_by_role("button", name="Upload avatar").click()
    page.wait_for_url(f"**{prefix}/settings")
    page.get_by_text("Avatar updated.", exact=True).wait_for()
    page.goto(f"{prefix}/users/Contestant")
    assert page.locator(".avatar-large").evaluate(
        "node => node.complete && node.naturalWidth > 0"
    )


@pytest.mark.parametrize("prefix", ["", "/minioj"])
def test_browser_contest_problem_submit_and_standing(
    browser, client, feature_data, monkeypatch, prefix
):
    monkeypatch.setattr(app, "root_path", prefix)
    cid = create_contest(feature_data)
    browser_login(browser, client, "Contestant")
    page = browser.new_page()
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{prefix}/contests/{cid}")
    with page.expect_response(
        lambda response: response.request.method == "POST"
    ) as mutation:
        page.get_by_role("button", name="Join contest").click()
    assert mutation.value.status == 303, mutation.value.text()
    page.wait_for_selector("text=Participating")
    page.locator(f'a[href="{prefix}/contests/{cid}/problems/A"]').click()
    page.locator("#code-editor").fill("int main(){}")
    page.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_url("**/submissions/*")
    assert "Round One" in page.locator("main").inner_text()
    page.goto(f"{prefix}/contests/{cid}/standings")
    assert page.get_by_role("heading", name="Standings", exact=True).is_visible()
    assert "Pending" in page.locator("table").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
