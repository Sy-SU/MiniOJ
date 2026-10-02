from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
import test_management
import test_management_browser
from fastapi import HTTPException
from test_phase1 import bearer, create_problem, create_user, csrf, login

from minioj.contest_performance import calculate_performance
from minioj.contests import save_contest, standings
from minioj.database import SessionLocal
from minioj.models import Contest, ContestParticipant, Problem, Submission, User
from minioj.server.main import app
from minioj.submissions import rejudge_submission

feature_data = test_management.feature_data
browser = test_management_browser.browser
chromium = test_management_browser.chromium
RATINGS = [800, 1200, 1600, 2000]
IDS = ["feature-one", "round-two", "round-three", "round-four"]


def test_performance_zero_to_all_is_strictly_increasing():
    values = [
        calculate_performance([(d, i < solved) for i, d in enumerate(RATINGS)])
        for solved in range(5)
    ]
    assert values == sorted(set(values))
    assert values[0] == 400 and values[-1] == 2400
    assert 1200 < values[2] < 1500 and 1600 < values[3] < 2000
    assert calculate_performance([(d, d == 2000) for d in RATINGS]) > values[1]


@pytest.mark.parametrize("state", list(itertools.product([False, True], repeat=4)))
def test_performance_uses_every_result_and_is_order_independent(state):
    items = list(zip(RATINGS, state, strict=True))
    original = items.copy()
    value = calculate_performance(items)
    assert type(value) is int and 0 <= value <= 4000
    assert value == calculate_performance(items[::-1]) == calculate_performance(items)
    assert items == original
    for i, solved in enumerate(state):
        if not solved:
            changed = items.copy()
            changed[i] = (RATINGS[i], True)
            assert calculate_performance(changed) > value
        for j in range(i + 1, 4):
            if solved and not state[j]:
                changed = items.copy()
                changed[i], changed[j] = (RATINGS[i], False), (RATINGS[j], True)
                assert calculate_performance(changed) > value


@pytest.mark.parametrize(
    "items,expected",
    [
        ([], 0),
        ([(None, False)], 800),
        ([(None, True)], 1600),
        ([(-(10**12), False)], 0),
        ([(10**12, True)], 4000),
    ],
)
def test_performance_empty_missing_and_extreme_ratings(items, expected):
    assert calculate_performance(items) == expected


def test_performance_includes_unsolved_distribution_and_missing_rating():
    first = [(800, True), (1200, True), (1600, False)]
    assert calculate_performance(first) > calculate_performance(first + [(2000, False)])
    assert calculate_performance(
        [(None, True), (2000, False)]
    ) == calculate_performance([(1200, True), (2000, False)])
    assert 0 <= calculate_performance([(-(10**12), False), (10**12, True)]) <= 4000


def make_round(data, state="RUNNING"):
    cid = test_management.create_contest(data, state=state)
    for problem_id in IDS[1:]:
        create_problem(data["admin"][0], problem_id)
    with SessionLocal() as db:
        for problem_id, rating in zip(IDS, RATINGS, strict=True):
            db.get(Problem, problem_id).rating = rating
        c = db.get(Contest, cid)
        save_contest(
            db,
            db.get(User, data["admin"][0]),
            c.title,
            c.description,
            c.start_time,
            c.end_time,
            IDS,
            cid,
        )
    return cid


def add_result(
    cid, uid, problem_id, *, verdict="AC", minute=10, status="FINISHED", practice=False
):
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        row = Submission(
            user_id=uid,
            problem_id=problem_id,
            contest_id=None if practice else cid,
            source_code="int main(){}",
            created_at=c.start_time + timedelta(minutes=minute),
            status=status,
            verdict=verdict if status == "FINISHED" else None,
            finished_at=datetime.now(UTC) if status == "FINISHED" else None,
            compile_result=json.dumps({"success": True})
            if status == "FINISHED"
            else None,
            judge_result=json.dumps({"verdict": verdict})
            if status == "FINISHED"
            else None,
        )
        db.add(row)
        db.commit()
        return row.id


def round_rows(cid):
    with SessionLocal() as db:
        return standings(db, db.get(Contest, cid))


def edit_form(client, cid, ids):
    token = csrf(client.get(f"/manage/contests/{cid}/edit").text)
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        return {
            "csrf_token": token,
            "title": c.title,
            "description": c.description,
            "start_time": c.start_time.isoformat(),
            "end_time": c.end_time.isoformat(),
            "problem_ids": "\n".join(ids),
        }


@pytest.mark.parametrize("state", ["UPCOMING", "RUNNING", "ENDED"])
@pytest.mark.parametrize(
    "role,name",
    [("user", "Contestant"), ("admin", "ContentMgr"), ("system", "SysOwner")],
)
def test_edit_problem_list_role_and_time_matrix(
    client, feature_data, role, name, state
):
    cid = make_round(feature_data, state)
    add_result(cid, feature_data["user"][0], IDS[2])
    login(client, name)
    token = csrf(client.get("/problems").text)
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        form = {
            "csrf_token": token,
            "title": "Edited",
            "start_time": c.start_time.isoformat(),
            "end_time": c.end_time.isoformat(),
            "problem_ids": f"{IDS[3]}\n{IDS[0]}",
        }
        actor = db.get(User, feature_data[role][0])
        if role == "user":
            with pytest.raises(HTTPException) as error:
                save_contest(
                    db, actor, c.title, "", c.start_time, c.end_time, [IDS[0]], cid
                )
            assert error.value.status_code == 403
    response = client.post(
        f"/manage/contests/{cid}/edit", data=form, follow_redirects=False
    )
    assert response.status_code == (403 if role == "user" else 303)
    with SessionLocal() as db:
        assert [p.problem_id for p in db.get(Contest, cid).problems] == (
            IDS if role == "user" else [IDS[3], IDS[0]]
        )
        assert db.query(Problem).count() == 4 and db.query(Submission).count() == 1


@pytest.mark.parametrize(
    "invalid", [[IDS[0], IDS[0]], ["not-present"], [IDS[2]], IDS * 26]
)
def test_edit_invalid_lists_are_atomic_and_csrf_protected(
    client, feature_data, invalid
):
    cid = make_round(feature_data)
    with SessionLocal() as db:
        db.get(Problem, IDS[2]).deleted_at = datetime.now(UTC)
        db.commit()
    login(client, "ContentMgr")
    form = edit_form(client, cid, invalid)
    assert (
        client.post(
            f"/manage/contests/{cid}/edit", data={**form, "csrf_token": "bad"}
        ).status_code
        == 403
    )
    assert client.post(f"/manage/contests/{cid}/edit", data=form).status_code == 422
    with SessionLocal() as db:
        assert [p.problem_id for p in db.get(Contest, cid).problems] == IDS


def test_edit_add_remove_reorder_readd_updates_current_standings(client, feature_data):
    cid = make_round(feature_data)
    uid = feature_data["user"][0]
    sid = add_result(cid, uid, IDS[2], minute=15)
    before = round_rows(cid)[0]
    login(client, "ContentMgr")
    path = f"/manage/contests/{cid}/edit"
    assert (
        client.post(
            path, data=edit_form(client, cid, [IDS[3], IDS[0], IDS[1]])
        ).status_code
        == 200
    )
    after = round_rows(cid)[0]
    assert list(after["problems"]) == [IDS[3], IDS[0], IDS[1]]
    assert (
        after["solved"] == after["penalty"] == 0
        and after["performance"] < before["performance"]
    )
    with SessionLocal() as db:
        s = db.get(Submission, sid)
        assert (
            s.verdict == "AC"
            and s.contest_id == cid
            and s.source_code == "int main(){}"
        )
        assert len(s.judge_runs) == 1 and db.get(Problem, IDS[2]) is not None
    # Readding a removed problem restores its in-window historical AC.
    assert (
        client.post(
            path, data=edit_form(client, cid, [IDS[2], IDS[3], IDS[0], IDS[1]])
        ).status_code
        == 200
    )
    readded = round_rows(cid)[0]
    assert readded["solved"] == 1 and readded["penalty"] == 15
    assert readded["performance"] == before["performance"]
    create_problem(feature_data["admin"][0], "new-hard")
    with SessionLocal() as db:
        db.get(Problem, "new-hard").rating = 2000
        db.commit()
    assert (
        client.post(path, data=edit_form(client, cid, IDS + ["new-hard"])).status_code
        == 200
    )
    added = round_rows(cid)[0]
    assert not added["problems"]["new-hard"]["solved"]
    assert added["performance"] < before["performance"]
    add_result(cid, uid, "new-hard", minute=20)
    assert round_rows(cid)[0]["performance"] > added["performance"]
    assert client.post(path, data=edit_form(client, cid, [])).status_code == 200
    empty = round_rows(cid)[0]
    assert (
        empty["problems"] == {}
        and empty["performance"] == empty["solved"] == empty["penalty"] == 0
    )
    with SessionLocal() as db:
        assert db.query(Submission).count() == 2 and db.query(Problem).count() == 5
    assert client.get(f"/contests/{cid}/standings").status_code == 200


def test_performance_not_used_for_rank_or_ties_and_api_contract(client, feature_data):
    cid = make_round(feature_data)
    hard_id, _ = create_user("HardSolver")
    tie_id, _ = create_user("TiedSolver")
    add_result(cid, feature_data["user"][0], IDS[0], minute=1)
    add_result(cid, hard_id, IDS[3], minute=2)
    add_result(cid, tie_id, IDS[3], minute=1)
    data = client.get(f"/api/v1/contests/{cid}/standings").json()
    assert set(data) == {"contest_id", "problems", "rows"}
    assert data["problems"] == [
        {"problem_id": p, "label": label} for p, label in zip(IDS, "ABCD", strict=True)
    ]
    rows = data["rows"]
    assert [r["username"] for r in rows] == ["Contestant", "TiedSolver", "HardSolver"]
    assert [r["rank"] for r in rows] == [1, 1, 3]
    assert rows[0]["performance"] < rows[-1]["performance"] == rows[1]["performance"]
    assert set(rows[0]) == {
        "rank",
        "user_id",
        "username",
        "solved",
        "penalty",
        "performance",
        "problems",
    }
    assert set(rows[0]["problems"][IDS[0]]) == {"solved", "wrong", "minute", "pending"}
    assert rows == round_rows(cid)
    assert "source_code" not in json.dumps(data) and "email" not in json.dumps(data)
    assert "<th>Performance</th>" in client.get(f"/contests/{cid}/standings").text
    path = "/api/v1/contests/{contest_id}/standings"
    schema = client.get("/openapi.json").json()
    assert schema["paths"][path]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ContestStandingsResponse"}
    response = client.get("/api/v1/contests/999999/standings")
    assert (
        response.status_code == 404 and response.json()["error"]["code"] == "not_found"
    )


def test_performance_current_ac_not_latest_attempt_practice_pending_or_old_run(
    client, feature_data
):
    cid = make_round(feature_data)
    uid = feature_data["user"][0]
    add_result(cid, uid, IDS[3], practice=True)
    add_result(cid, uid, IDS[2], minute=-1)
    add_result(cid, uid, IDS[1], minute=120)
    add_result(cid, uid, IDS[1], verdict="IE")
    with SessionLocal() as db:
        db.add(ContestParticipant(contest_id=cid, user_id=uid))
        db.commit()
    initial = round_rows(cid)[0]
    assert initial["performance"] == 400 and initial["solved"] == 0
    first = add_result(cid, uid, IDS[0], verdict="WA", minute=3)
    accepted = add_result(cid, uid, IDS[0], minute=5)
    add_result(cid, uid, IDS[0], verdict="WA", minute=6)
    add_result(cid, uid, IDS[3], status="RUNNING", minute=8)
    solved = round_rows(cid)[0]
    assert solved["solved"] == 1 and solved["penalty"] == 25
    assert solved["performance"] == calculate_performance(
        [(d, i == 0) for i, d in enumerate(RATINGS)]
    )
    with SessionLocal() as db:
        c = db.get(Contest, cid)
        actor = db.get(User, feature_data["admin"][0])
        rejudge_submission(db, accepted, actor)
        assert standings(db, c)[0]["performance"] == initial["performance"]
        current = db.get(Submission, accepted)
        current.status, current.verdict = "FINISHED", "WA"
        db.commit()
        assert standings(db, c)[0]["performance"] == initial["performance"]
        # A different submission acquiring AC also restores solved/performance.
        rejudge_submission(db, first, actor)
        current = db.get(Submission, first)
        current.status, current.verdict = "FINISHED", "AC"
        db.commit()
        row = standings(db, c)[0]
        assert row["performance"] == solved["performance"] and row["penalty"] == 3
        assert db.get(Submission, accepted).judge_runs[0].verdict == "AC"


def test_api_upcoming_visibility_session_bearer_and_rating_edits(client, feature_data):
    cid = make_round(feature_data, "UPCOMING")
    with SessionLocal() as db:
        db.add(ContestParticipant(contest_id=cid, user_id=feature_data["user"][0]))
        db.commit()
    path = f"/api/v1/contests/{cid}/standings"
    public = client.get(path).json()
    assert public["problems"] == [] and public["rows"][0]["performance"] == 0
    assert client.get(path, headers=bearer(feature_data["user"][1])).json() == public
    login(client, "ContentMgr")
    admin = client.get(path).json()
    assert admin["problems"] and admin["rows"][0]["performance"] == 400
    assert client.get(path, headers=bearer(feature_data["admin"][1])).json() == admin
    with SessionLocal() as db:
        db.get(Problem, IDS[0]).rating = None
        db.commit()
    assert client.get(path).json()["rows"][0]["performance"] == 800
    with SessionLocal() as db:
        db.get(Contest, cid).deleted_at = datetime.now(UTC)
        db.commit()
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("width", [1365, 390])
@pytest.mark.parametrize("name", ["ContentMgr", "SysOwner"])
def test_browser_edit_existing_running_problems_and_performance(
    browser, client, feature_data, monkeypatch, prefix, width, name, tmp_path
):
    monkeypatch.setattr(app, "root_path", prefix)
    cid = make_round(feature_data)
    add_result(cid, feature_data["user"][0], IDS[0])
    before = round_rows(cid)[0]["performance"]
    test_management_browser.browser_login(browser, client, name)
    page = browser.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{prefix}/manage/contests/{cid}/edit")
    assert page.locator("#contest-problem-ids").input_value() == "\n".join(IDS)
    assert page.locator("#contest-problem-controls").is_visible()
    page.get_by_role("button", name=f"Move up {IDS[3]}", exact=True).click()
    page.get_by_role("button", name=f"Remove {IDS[1]}", exact=True).click()
    page.get_by_label("Existing problem ID").fill(IDS[1])
    page.get_by_label("Existing problem ID").press("Enter")
    page.get_by_label("Existing problem ID").fill(IDS[1])
    page.get_by_role("button", name="Add problem", exact=True).click()
    assert "already" in page.locator("#contest-problem-status").inner_text()
    page.get_by_label("Existing problem ID").fill("<script>")
    page.get_by_role("button", name="Add problem", exact=True).click()
    assert "valid problem ID" in page.locator("#contest-problem-status").inner_text()
    ordered = [IDS[0], IDS[3], IDS[2], IDS[1]]
    assert page.locator("#contest-problem-ids").input_value() == "\n".join(ordered)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if name == "ContentMgr" and not prefix:
        page.screenshot(
            path=str(tmp_path / f"contest-editor-{width}.png"), full_page=True
        )
    page.get_by_role("button", name="Save contest", exact=True).click()
    page.wait_for_url(f"**/manage/contests/{cid}/edit")
    page.reload()
    assert page.locator("#contest-problem-ids").input_value() == "\n".join(ordered)
    assert list(round_rows(cid)[0]["problems"]) == ordered
    assert round_rows(cid)[0]["performance"] == before
    page.get_by_role("button", name=f"Remove {IDS[0]}", exact=True).click()
    page.get_by_role("button", name="Save contest", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.goto(f"{prefix}/contests/{cid}/standings")
    assert page.locator("thead th").all_text_contents() == [
        "Rank",
        "User",
        "Solved",
        "Penalty",
        "Performance",
        "A",
        "B",
        "C",
    ]
    assert page.locator("tbody tr").first.locator("td").nth(4).inner_text() == str(
        round_rows(cid)[0]["performance"]
    )
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if name == "ContentMgr" and not prefix:
        page.screenshot(
            path=str(tmp_path / f"contest-standings-{width}.png"), full_page=True
        )


def test_browser_no_js_editor_round_trip(browser, client, feature_data):
    cid = make_round(feature_data)
    page = browser.new_page()
    page.route("**/static/contest_form.js", lambda route: route.abort())
    test_management_browser.browser_login(browser, client, "ContentMgr")
    page.goto(f"/manage/contests/{cid}/edit")
    assert page.get_by_label("Problem IDs in order").is_visible()
    page.get_by_label("Problem IDs in order").fill(f"{IDS[2]}\n{IDS[0]}")
    page.get_by_role("button", name="Save contest", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert (
        page.get_by_label("Problem IDs in order").input_value() == f"{IDS[2]}\n{IDS[0]}"
    )
    with SessionLocal() as db:
        assert [p.problem_id for p in db.get(Contest, cid).problems] == [IDS[2], IDS[0]]


def test_edit_current_association_guards_new_submissions_without_canceling_old(
    client, feature_data
):
    cid = make_round(feature_data)
    login(client, "ContentMgr")
    path = f"/manage/contests/{cid}/edit"
    payload = {"problem_id": IDS[0], "source_code": "int main(){}"}
    headers = bearer(feature_data["user"][1])
    accepted = client.post(
        f"/api/v1/contests/{cid}/submissions", json=payload, headers=headers
    )
    assert accepted.status_code == 202
    sid = accepted.json()["submission_id"]
    assert client.post(path, data=edit_form(client, cid, IDS[1:])).status_code == 200
    assert (
        client.post(
            f"/api/v1/contests/{cid}/submissions", json=payload, headers=headers
        ).status_code
        == 422
    )
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        assert row.contest_id == cid and row.status == "QUEUED"
        assert row.source_code == payload["source_code"] and len(row.judge_runs) == 1
    assert client.post(path, data=edit_form(client, cid, IDS)).status_code == 200
    assert (
        client.post(
            f"/api/v1/contests/{cid}/submissions", json=payload, headers=headers
        ).status_code
        == 202
    )


def test_contest_problem_limit_boundary_and_unique_positions(client, feature_data):
    cid = make_round(feature_data)
    ids = [f"many-{i:03}" for i in range(101)]
    with SessionLocal() as db:
        db.add_all(
            [
                Problem(
                    id=pid,
                    title=pid,
                    statement="Test",
                    created_by=feature_data["admin"][0],
                )
                for pid in ids
            ]
        )
        db.commit()
        c = db.get(Contest, cid)
        actor = db.get(User, feature_data["admin"][0])
        save_contest(
            db, actor, c.title, c.description, c.start_time, c.end_time, ids[:100], cid
        )
        assert [p.position for p in c.problems] == list(range(1, 101))
        with pytest.raises(HTTPException) as error:
            save_contest(
                db, actor, c.title, c.description, c.start_time, c.end_time, ids, cid
            )
        assert error.value.status_code == 422
        db.rollback()
        assert [p.problem_id for p in c.problems] == ids[:100]
