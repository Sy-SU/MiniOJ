from __future__ import annotations

import copy
import json
from dataclasses import replace
from html.parser import HTMLParser

import pytest
import test_management_browser
import test_problem_pages
from test_phase1 import create_problem, create_user, login

from minioj.config import settings
from minioj.database import SessionLocal
from minioj.feedback import submission_testcase_rows
from minioj.models import Submission
from minioj.server.main import app

chromium = test_problem_pages.chromium
browser_context = test_problem_pages.browser_context
browser = test_management_browser.browser


def point_result(index, verdict="AC", time_ms=1, memory_kb=1024):
    return {
        "test_index": index,
        "verdict": verdict,
        "time_ms": time_ms,
        "memory_kb": memory_kb,
    }


def result(records, *, verdict="AC", total=None, passed=None):
    return {
        "verdict": verdict,
        "summary": f"{verdict}. Test results.",
        "tests": {
            "total": len(records) if total is None else total,
            "passed": len(records) if passed is None else passed,
            "failed_test": None if verdict == "AC" else len(records),
        },
        "resources": {"time_ms": 987654, "memory_kb": 765432},
        "test_results": records,
        "testcase_types": ["sample"] * (len(records) if total is None else total),
    }


def submission(client, feedback, *, verdict="AC", status="FINISHED"):
    owner_id, _ = create_user("PointOwner")
    create_problem(owner_id, "point-results")
    with SessionLocal() as db:
        row = Submission(
            user_id=owner_id,
            problem_id="point-results",
            source_code="int main(){}",
            status=status,
            verdict=verdict if status == "FINISHED" else None,
            compile_result=json.dumps({"success": verdict != "CE"})
            if status == "FINISHED"
            else None,
            judge_result=json.dumps(feedback) if feedback is not None else None,
        )
        db.add(row)
        db.commit()
        submission_id = row.id
    login(client, "PointOwner")
    return submission_id


class TableRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.table = False
        self.row = None
        self.cell = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if (
            tag == "table"
            and "testcase-results-table" in attrs.get("class", "").split()
        ):
            self.table = True
        elif self.table and tag == "tr" and "data-test-index" in attrs:
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            self.cell = ""

    def handle_data(self, text):
        if self.cell is not None:
            self.cell += text

    def handle_endtag(self, tag):
        if self.cell is not None and tag in {"td", "th"}:
            self.row.append(self.cell.strip())
            self.cell = None
        elif self.row is not None and tag == "tr":
            self.rows.append(self.row)
            self.row = None
        elif self.table and tag == "table":
            self.table = False


def test_rows_are_sorted_and_use_individual_not_aggregate_measurements():
    feedback = result(
        [
            point_result(2, time_ms=0, memory_kb=None),
            point_result(1, time_ms=13, memory_kb=0),
        ]
    )
    original = copy.deepcopy(feedback)
    assert submission_testcase_rows(feedback) == [
        {"test_index": 1, "status": "AC", "time_ms": 13, "memory_kb": 0},
        {"test_index": 2, "status": "AC", "time_ms": 0, "memory_kb": None},
    ]
    assert feedback == original


def test_failed_submission_marks_unexecuted_tests_without_measurements():
    rows = submission_testcase_rows(
        result(
            [point_result(1), point_result(2, "TLE", 205, 2048)],
            verdict="TLE",
            total=4,
            passed=1,
        )
    )
    assert [r["status"] for r in rows] == ["AC", "TLE", "NOT_RUN", "NOT_RUN"]
    assert rows[2] == {
        "test_index": 3,
        "status": "NOT_RUN",
        "time_ms": None,
        "memory_kb": None,
    }


def test_legacy_and_partial_metadata_are_not_filled_with_aggregate_resources():
    feedback = result([], total=3, passed=3)
    assert [r["status"] for r in submission_testcase_rows(feedback)] == [
        "NOT_RECORDED"
    ] * 3
    del feedback["test_results"]
    assert submission_testcase_rows(feedback) == []
    assert submission_testcase_rows({"verdict": "AC", "summary": "Accepted"}) == []


def test_malformed_metadata_is_sanitized_without_raising():
    feedback = result(
        [
            None,
            {"test_index": True},
            {"test_index": "1"},
            point_result(1, verdict=["AC"], time_ms=-1, memory_kb=True),
            point_result(
                2, verdict="<script>bad()</script>", time_ms="5", memory_kb=1.5
            ),
        ],
        total=2,
        passed=2,
    )
    rows = submission_testcase_rows(feedback)
    assert len(rows) == 2
    assert all(
        row["status"] == "NOT_RECORDED"
        and row["time_ms"] is None
        and row["memory_kb"] is None
        for row in rows
    )


@pytest.mark.parametrize("prefix", ["", "/minioj"])
def test_accepted_detail_displays_all_34_points(client, monkeypatch, prefix):
    feedback = result(
        [point_result(i, time_ms=i, memory_kb=i * 1024) for i in range(1, 35)]
    )
    sid = submission(client, feedback)
    monkeypatch.setattr(app, "root_path", prefix)
    response = client.get(f"{prefix}/submissions/{sid}")
    assert response.status_code == 200
    rows = TableRows(response.text).rows
    assert len(rows) == 34
    assert rows[0] == ["#1", "AC", "1 ms", "1024 KB"]
    assert rows[-1] == ["#34", "AC", "34 ms", "34816 KB"]


@pytest.mark.parametrize("verdict", ["WA", "RE", "TLE", "MLE", "OLE", "IE"])
def test_failed_point_and_not_run_rows_are_displayed(client, verdict):
    sid = submission(
        client,
        result(
            [point_result(1), point_result(2, verdict, 0, None)],
            verdict=verdict,
            total=4,
            passed=1,
        ),
        verdict=verdict,
    )
    page = client.get(f"/submissions/{sid}")
    assert TableRows(page.text).rows == [
        ["#1", "AC", "1 ms", "1024 KB"],
        ["#2", verdict, "0 ms", "—"],
        ["#3", "Not run", "—", "—"],
        ["#4", "Not run", "—", "—"],
    ]
    assert "First failed test:" not in page.text
    assert "Judging stops at the first failed test." not in page.text


def test_ce_does_not_claim_runtime_measurements(client):
    sid = submission(client, result([], verdict="CE", total=2, passed=0), verdict="CE")
    assert TableRows(client.get(f"/submissions/{sid}").text).rows == [
        ["#1", "Not run", "—", "—"],
        ["#2", "Not run", "—", "—"],
    ]


def test_old_submission_shows_measurements_missing_notice(client):
    feedback = result([], total=34, passed=34)
    del feedback["test_results"]
    sid = submission(client, feedback)
    page = client.get(f"/submissions/{sid}")
    assert TableRows(page.text).rows == []
    assert "Per-test measurements were not stored" in page.text
    assert "Resubmit to collect them." in page.text


@pytest.mark.parametrize("policy", ["full", "diagnostic", "verdict_only"])
def test_per_point_ui_respects_existing_feedback_policy(client, monkeypatch, policy):
    feedback = result([point_result(1)], total=1)
    feedback["test_results"][0].update(
        input="PRIVATE_ROW_INPUT",
        expected="PRIVATE_ROW_ANSWER",
        stderr="PRIVATE_CHECKER_DIAGNOSTIC",
    )
    sid = submission(client, feedback)
    monkeypatch.setattr(
        "minioj.server.web.settings", replace(settings, feedback_policy=policy)
    )
    page = client.get(f"/submissions/{sid}")
    rows = TableRows(page.text).rows
    assert len(rows) == (0 if policy == "verdict_only" else 1)
    assert "PRIVATE_ROW" not in page.text
    assert "PRIVATE_CHECKER_DIAGNOSTIC" not in page.text
    if policy == "verdict_only":
        assert "Per-test results are hidden by the active Feedback Mode." in page.text


def test_submission_permissions_are_unchanged(client):
    sid = submission(client, result([point_result(1)]))
    create_user("PointOther")
    login(client, "PointOther")
    assert client.get(f"/submissions/{sid}").status_code == 404
    create_user("PointAdmin", role="admin")
    login(client, "PointAdmin")
    assert len(TableRows(client.get(f"/submissions/{sid}").text).rows) == 1


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize(
    "viewport",
    [{"width": 1280, "height": 900}, {"width": 390, "height": 844}],
    ids=["desktop", "mobile"],
)
def test_browser_table_scroll_and_polling_completion(
    browser_context, client, monkeypatch, prefix, viewport, tmp_path
):
    sid = submission(client, None, status="QUEUED")
    monkeypatch.setattr(app, "root_path", prefix)
    page = browser_context.new_page()
    page.set_viewport_size(viewport)
    page.goto(f"http://testserver{prefix}/submissions/{sid}")
    assert page.locator("#submission-progress").count() == 1
    assert page.locator(".testcase-results-table").count() == 0
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        row.status, row.verdict = "FINISHED", "AC"
        row.compile_result = json.dumps({"success": True})
        row.judge_result = json.dumps(
            result(
                [point_result(i, time_ms=i, memory_kb=i * 1024) for i in range(1, 35)]
            )
        )
        db.commit()
    page.locator("#sample-results").wait_for(timeout=10000)
    assert page.locator("#submission-verdict").inner_text() == "AC"
    assert page.locator("#sample-results tbody tr").count() == 34
    assert page.locator(
        '#sample-results tr[data-test-index="34"]'
    ).inner_text().split() == ["#34", "AC", "34", "ms", "34816", "KB"]
    wrapper = page.locator(".testcase-results-wrap")
    assert wrapper.evaluate("el => el.scrollWidth <= el.clientWidth")
    page.screenshot(path=str(tmp_path / "testcase-results.png"), full_page=True)
    wrapper.focus()
    page.keyboard.press("End")
    page.wait_for_function(
        "document.querySelector('.testcase-results-wrap').scrollTop > 0"
    )
    assert wrapper.evaluate("el => el.scrollHeight > el.clientHeight")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.locator("#submission-progress").count() == 0


@pytest.mark.parametrize("sample", [False, True])
def test_failure_content_is_sample_only_and_byte_bounded(client, sample):
    feedback = result([point_result(1, "WA")], verdict="WA", passed=0)
    feedback["failure"] = {
        "test_index": 1,
        "is_sample": sample,
        "input": "中" * 400 + "PRIVATE_TAIL",
        "expected": "e" * 1024 + "EXPECTED_TAIL",
        "actual": "a" * 1024 + "ACTUAL_TAIL",
        "stderr": "SECRET_DIAGNOSTIC",
    }
    sid = submission(client, feedback, verdict="WA")
    from minioj.feedback import submission_feedback

    with SessionLocal() as db:
        filtered = submission_feedback(db.get(Submission, sid), "full")
    failure = filtered["failure"]
    if sample:
        assert len(failure["input"].encode()) == 1023
        assert len(failure["expected"].encode()) == 1024
        assert failure["input_truncated"] is True
    else:
        assert failure == {"test_index": 1}
    html = client.get(f"/submissions/{sid}").text
    assert "PRIVATE_TAIL" not in html
    assert "EXPECTED_TAIL" not in html
    assert "ACTUAL_TAIL" not in html
    assert ("failure-grid" in html) is sample


def grouped_submission(client):
    feedback = result([point_result(1, "WA")], verdict="WA", total=4, passed=0)
    feedback["testcase_types"] = ["hidden", "sample", "generated", "sample"]
    feedback["failure"] = {
        "test_index": 1,
        "is_sample": False,
        "input": "HIDDEN_INPUT" + "h" * 1024 + "HIDDEN_TAIL",
        "expected": "HIDDEN_ANSWER" + "e" * 1024 + "EXPECTED_TAIL",
        "actual": "HIDDEN_ACTUAL" + "a" * 1024 + "ACTUAL_TAIL",
    }
    sid = submission(client, feedback, verdict="WA")
    from minioj.problems import add_testcase

    with SessionLocal() as db:
        row = db.get(Submission, sid)
        problem = row.problem
        for kind, content in [
            ("hidden", "HIDDEN_INPUT"),
            ("sample", "SAMPLE_INPUT"),
            ("generated", "GENERATED_INPUT"),
            ("sample", "SECOND_SAMPLE"),
        ]:
            add_testcase(
                db, problem, kind, content + "中" * 400 + "FILE_TAIL", "answer"
            )
        row.problem_revision = problem.revision
        db.commit()
    return sid


def test_grouped_results_keep_metadata_and_enforce_content_permissions(client):
    sid = grouped_submission(client)
    html = client.get(f"/submissions/{sid}").text
    assert 'id="sample-results"' in html
    assert 'id="testcase-results"' in html
    assert 'id="sample-results-heading">Sample results</h3>' in html
    assert 'id="testcase-results-heading">Testcase results</h3>' in html
    assert TableRows(html).rows == [
        ["#2", "Not run", "—", "—"],
        ["#4", "Not run", "—", "—"],
        ["#1", "WA", "1 ms", "1024 KB"],
        ["#3", "Not run", "—", "—"],
    ]
    assert "SAMPLE_INPUT" in html and "SECOND_SAMPLE" in html
    assert "HIDDEN_INPUT" not in html and "GENERATED_INPUT" not in html
    assert "HIDDEN_ACTUAL" not in html
    assert 'aria-controls="testcase-preview-1"' not in html
    assert "View data for" not in html
    assert "First failed test:" not in html
    assert "FILE_TAIL" not in html

    create_user("PreviewAdmin", role="admin")
    login(client, "PreviewAdmin")
    admin_html = client.get(f"/submissions/{sid}").text
    assert 'id="sample-results"' in admin_html
    assert 'id="testcase-results"' in admin_html
    assert [row[0] for row in TableRows(admin_html).rows] == ["#2", "#4", "#1", "#3"]
    assert "HIDDEN_INPUT" in admin_html and "GENERATED_INPUT" in admin_html
    assert "HIDDEN_ACTUAL" in admin_html
    assert 'aria-controls="testcase-preview-1"' in admin_html
    for marker in ("FILE_TAIL", "HIDDEN_TAIL", "EXPECTED_TAIL", "ACTUAL_TAIL"):
        assert marker not in admin_html


def test_changed_problem_does_not_attach_new_data_to_old_results(client):
    sid = grouped_submission(client)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        row.problem.revision += 1
        db.commit()
    html = client.get(f"/submissions/{sid}").text
    assert "SAMPLE_INPUT" not in html
    assert "SECOND_SAMPLE" not in html
    assert [row[0] for row in TableRows(html).rows] == ["#2", "#4", "#1", "#3"]


def test_diagnostic_mode_does_not_embed_card_previews(client, monkeypatch):
    sid = grouped_submission(client)
    monkeypatch.setattr(
        "minioj.server.web.settings", replace(settings, feedback_policy="diagnostic")
    )
    html = client.get(f"/submissions/{sid}").text
    assert "SAMPLE_INPUT" not in html
    assert len(TableRows(html).rows) == 4
    assert "testcase-preview-row" not in html


@pytest.mark.parametrize("role", ["user", "admin", "system"])
@pytest.mark.parametrize("policy", ["full", "diagnostic", "verdict_only"])
def test_mixed_result_metadata_and_preview_policy_matrix(
    client, monkeypatch, role, policy
):
    sid = grouped_submission(client)
    with SessionLocal() as db:
        db.get(Submission, sid).user.role = role
        db.commit()
    monkeypatch.setattr(
        "minioj.server.web.settings", replace(settings, feedback_policy=policy)
    )
    path = f"/manage/submissions/{sid}" if role != "user" else f"/submissions/{sid}"
    response = client.get(path)
    assert response.status_code == 200
    html = response.text
    assert len(TableRows(html).rows) == (0 if policy == "verdict_only" else 4)
    for title in ("Sample results", "Testcase results"):
        assert (f">{title}</h3>" in html) == (policy != "verdict_only")
    assert ("SAMPLE_INPUT" in html) == (policy == "full")
    for marker in ("HIDDEN_INPUT", "HIDDEN_ANSWER", "HIDDEN_ACTUAL", "GENERATED_INPUT"):
        assert (marker in html) == (role != "user" and policy == "full")
    assert ('aria-controls="testcase-preview-1"' in html) == (
        role != "user" and policy == "full"
    )


def test_user_non_sample_status_does_not_read_private_files(client, monkeypatch):
    from minioj import feedback

    sid = grouped_submission(client)
    reads = []
    original = feedback._testcase_path

    def checked_path(case, path):
        reads.append(case.type)
        assert case.type == "sample"
        return original(case, path)

    monkeypatch.setattr(feedback, "_testcase_path", checked_path)
    html = client.get(f"/submissions/{sid}").text
    assert len(TableRows(html).rows) == 4
    assert reads == ["sample"] * 4
    assert "HIDDEN_INPUT" not in html and "GENERATED_INPUT" not in html


@pytest.mark.parametrize(
    "kinds,table",
    [(["sample", "sample"], "sample"), (["hidden", "generated"], "testcase")],
)
def test_empty_result_sections_are_not_rendered(client, kinds, table):
    feedback = result([point_result(1), point_result(2)])
    feedback["testcase_types"] = kinds
    sid = submission(client, feedback)
    html = client.get(f"/submissions/{sid}").text
    assert f'id="{table}-results"' in html
    other = "sample" if table == "testcase" else "testcase"
    assert f'id="{other}-results"' not in html
    assert len(TableRows(html).rows) == 2


def test_legacy_unknown_classification_keeps_all_statuses_without_preview(client):
    feedback = result(
        [point_result(1), point_result(2, "WA")], verdict="WA", total=3, passed=1
    )
    del feedback["testcase_types"]
    sid = submission(client, feedback, verdict="WA")
    html = client.get(f"/submissions/{sid}").text
    assert 'id="testcase-results"' in html and 'id="sample-results"' not in html
    assert [row[1] for row in TableRows(html).rows] == ["AC", "WA", "Not run"]
    assert "testcase-preview-toggle" not in html


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("role", ["user", "admin", "system"])
@pytest.mark.parametrize("prefix", ["", "/minioj"])
def test_number_preview_browser_layout(
    browser, client, monkeypatch, width, role, prefix, tmp_path
):
    sid = grouped_submission(client)
    admin = role != "user"
    username = "PointOwner"
    if admin:
        username = "ClickAdmin" if role == "admin" else "ClickSys"
        create_user(username, role=role)
    monkeypatch.setattr(app, "root_path", prefix)
    test_management_browser.browser_login(browser, client, username)
    page = browser.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    path = "/manage" if admin else ""
    page.goto(f"{prefix}{path}/submissions/{sid}")
    assert page.get_by_role("heading", name="Sample results", exact=True).is_visible()
    assert page.get_by_role("heading", name="Testcase results", exact=True).is_visible()
    assert page.locator("#sample-results tr[data-test-index]").count() == 2
    assert page.locator("#testcase-results tr[data-test-index]").count() == 2
    assert page.locator(
        '#testcase-results tr[data-test-index="1"]'
    ).inner_text().split() == ["#1", "WA", "1", "ms", "1024", "KB"]
    index = 1 if admin else 2
    toggle = page.get_by_role("button", name=f"Preview test #{index}", exact=True)
    preview = page.locator(f"#testcase-preview-{index}")
    assert not preview.is_visible()
    toggle.click()
    assert preview.is_visible()
    assert toggle.get_attribute("aria-expanded") == "true"
    assert page.get_by_role(
        "button", name="Preview test #1", exact=True
    ).count() == int(admin)
    for marker in ("HIDDEN_INPUT", "HIDDEN_ANSWER", "HIDDEN_ACTUAL", "GENERATED_INPUT"):
        assert (marker in page.content()) == admin
    if not admin:
        assert (
            page.locator('#testcase-results tr[data-test-index="3"] button').count()
            == 0
        )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(tmp_path / "grouped-results.png"), full_page=True)
    toggle.press("Enter")
    assert not preview.is_visible()
    assert toggle.get_attribute("aria-expanded") == "false"
    assert "View data for" not in page.content()
