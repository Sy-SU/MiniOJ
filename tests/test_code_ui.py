from __future__ import annotations

import json
from html.parser import HTMLParser

import pytest
import test_management
import test_management_browser
from test_management import create_contest, make_submission
from test_submission_results import point_result, result, submission

from minioj.database import SessionLocal
from minioj.feedback import submission_feedback
from minioj.models import Problem, Submission
from minioj.problems import add_testcase
from minioj.server.main import app

browser = test_management_browser.browser
chromium = test_management_browser.chromium
feature_data = test_management.feature_data


class DataPreview(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.current = None
        self.values = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "tr" and dict(attrs).get("id") == "testcase-preview-1":
            self.active = True
        if tag == "pre" and self.active:
            self.current = ""

    def handle_data(self, data):
        if self.current is not None:
            self.current += data

    def handle_endtag(self, tag):
        if tag == "pre" and self.current is not None:
            self.values.append(self.current)
            self.current = None
        if tag == "tr":
            self.active = False


@pytest.mark.parametrize("failing", [False, True], ids=["file", "stored-failure"])
@pytest.mark.parametrize(
    "content",
    ["", "x" * 1023, "x" * 1024, "x" * 1025, "🙂" * 256, "中" * 400],
    ids=["empty", "below", "exact", "over", "unicode-exact", "unicode-over"],
)
def test_preview_ellipsis_only_when_data_is_truncated(client, failing, content):
    verdict = "WA" if failing else "AC"
    feedback = result([point_result(1, verdict)], verdict=verdict)
    if failing:
        feedback["failure"] = {
            "test_index": 1,
            "is_sample": True,
            "input": content,
            "expected": content,
            "actual": content,
        }
    sid = submission(client, feedback, verdict=verdict)
    with SessionLocal() as db:
        row = db.get(Submission, sid)
        add_testcase(db, row.problem, "sample", content, content)
        row.problem_revision = row.problem.revision
        db.commit()
        filtered = submission_feedback(row, "full")
        assert json.loads(row.judge_result) == feedback
    raw = content.encode()
    bounded = raw[:1024].decode("utf-8", errors="ignore")
    display = bounded + ("..." if len(raw) > 1024 else "")
    assert DataPreview(client.get(f"/submissions/{sid}").text).values == [display] * (
        3 if failing else 2
    )
    if failing:
        # Ellipsis is presentation only, not extra API data bytes.
        assert filtered["failure"]["input"] == bounded
        assert filtered["failure"]["input_truncated"] == (len(raw) > 1024)


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("where", ["practice", "contest", "standard"])
def test_code_editor_tab_indentation_and_focus_escape(
    browser, client, feature_data, monkeypatch, prefix, where
):
    monkeypatch.setattr(app, "root_path", prefix)
    with SessionLocal() as db:
        add_testcase(db, db.get(Problem, "feature-one"), "sample", "1", "2")
    name = "ContentMgr" if where == "standard" else "Contestant"
    test_management_browser.browser_login(browser, client, name)
    path = "/problems/feature-one"
    if where == "contest":
        path = f"/contests/{create_contest(feature_data)}/problems/A"
    elif where == "standard":
        path = "/manage/problems/feature-one/edit"
    page = browser.new_page()
    page.goto(prefix + path)
    editor = page.locator("#standard-source" if where == "standard" else "#code-editor")
    original = "int main() {\nreturn 0;\n}\n"
    editor.fill(original)
    position = len("int main() {\n")
    editor.evaluate("(el, pos) => el.setSelectionRange(pos, pos)", position)
    editor.press("Tab")
    assert editor.input_value() == "int main() {\n    return 0;\n}\n"
    assert editor.evaluate("el => el === document.activeElement")
    editor.press("Shift+Tab")
    assert editor.input_value() == original

    # A selection ending at a new line must not indent the next unselected line.
    editor.fill("first\n  second\nlast")
    editor.evaluate("el => el.setSelectionRange(0, 15)")
    editor.press("Tab")
    assert editor.input_value() == "    first\n      second\nlast"
    editor.press("Shift+Tab")
    assert editor.input_value() == "first\n  second\nlast"

    editor.fill("\treturn 0;\n")
    editor.evaluate("el => el.setSelectionRange(2, 2)")
    editor.press("Shift+Tab")
    assert editor.input_value() == "return 0;\n"
    editor.press("Escape")
    page.keyboard.press("Tab")
    assert not editor.evaluate("el => el === document.activeElement")
    if where != "standard":
        assert page.locator("#sample-select").evaluate(
            "el => el === document.activeElement"
        )
        custom = page.locator("#custom-input")
        custom.fill("1")
        custom.press("Tab")
        assert custom.input_value() == "1"
        assert not custom.evaluate("el => el === document.activeElement")


def test_code_editor_native_undo_fallback_and_submitted_value(
    browser, client, feature_data
):
    test_management_browser.browser_login(browser, client, "Contestant")
    page = browser.new_page()
    page.goto("/problems/feature-one")
    editor = page.locator("#code-editor")
    editor.fill("int main(){}")
    editor.evaluate("el => el.setSelectionRange(0, 0)")
    editor.press("Tab")
    editor.press("Control+z")
    assert editor.input_value() == "int main(){}"
    page.evaluate("document.execCommand = () => false")
    editor.evaluate("el => el.setSelectionRange(0, 0)")
    editor.press("Tab")
    assert editor.input_value() == "    int main(){}"
    page.get_by_role("button", name="Submit", exact=True).click()
    page.wait_for_url("**/submissions/*")
    with SessionLocal() as db:
        assert db.query(Submission).one().source_code == "    int main(){}"


SOURCE = """#include <iostream>
// 中文 and Unicode Ω remain unchanged.
int main() {
    const char* text = "<img src=x onerror=window.sourceExecuted=true>";
    /* </code><script>window.sourceExecuted=true</script> */
    std::cout << text << 42;
    return 0;
}
"""


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("managed,width", [(False, 1365), (False, 390), (True, 390)])
def test_source_highlight_copy_privacy_and_layout(
    browser, client, feature_data, monkeypatch, prefix, managed, width, tmp_path
):
    monkeypatch.setattr(app, "root_path", prefix)
    sid = make_submission(feature_data, verdict="AC")
    with SessionLocal() as db:
        db.get(Submission, sid).source_code = SOURCE
        db.commit()
    test_management_browser.browser_login(
        browser, client, "ContentMgr" if managed else "Contestant"
    )
    browser.grant_permissions(["clipboard-read", "clipboard-write"])
    page = browser.new_page()
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(f"{prefix}{'/manage' if managed else ''}/submissions/{sid}")
    code = page.locator("#submission-source")
    assert code.text_content() == SOURCE
    for kind in ("keyword", "string", "comment", "preprocessor", "number"):
        assert code.locator(f".syntax-{kind}").count() > 0
    assert code.locator("img,script").count() == 0
    assert not page.evaluate("Boolean(window.sourceExecuted)")
    page.get_by_role("button", name="Copy code", exact=True).click()
    page.get_by_text("Source code copied.", exact=True).wait_for()
    assert page.evaluate("navigator.clipboard.readText()") == SOURCE
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    if not prefix and not managed:
        page.screenshot(
            path=str(tmp_path / f"source-preview-{width}.png"), full_page=True
        )
    other, _ = test_management.create_user("CodeOther")
    assert other != feature_data["user"][0]
    test_management_browser.browser_login(browser, client, "CodeOther")
    response = page.goto(f"{prefix}/submissions/{sid}")
    assert response.status == 404 and SOURCE not in page.content()


@pytest.mark.parametrize("mode", ["denied", "insecure", "missing", "failed"])
def test_source_copy_fallback_and_failure(browser, client, feature_data, mode):
    sid = make_submission(feature_data, verdict="AC")
    test_management_browser.browser_login(browser, client, "Contestant")
    browser.grant_permissions(["clipboard-read", "clipboard-write"])
    page = browser.new_page()
    page.goto(f"/submissions/{sid}")
    page.evaluate(
        "window.readClipboard = navigator.clipboard.readText.bind(navigator.clipboard)"
    )
    if mode == "denied":
        page.evaluate(
            "() => { navigator.clipboard.writeText = () => Promise.reject(new Error('denied')); }"
        )
    elif mode == "insecure":
        page.evaluate(
            "Object.defineProperty(window, 'isSecureContext', {value: false})"
        )
    else:
        page.evaluate(
            "Object.defineProperty(navigator, 'clipboard', {value: undefined})"
        )
    if mode == "failed":
        page.evaluate("document.execCommand = () => false")
    page.get_by_role("button", name="Copy code", exact=True).click()
    if mode == "failed":
        page.get_by_text("Could not copy automatically.", exact=False).wait_for()
        assert page.get_by_role("button", name="Copy code", exact=True).is_enabled()
    else:
        page.get_by_text("Source code copied.", exact=True).wait_for()
        assert page.evaluate("window.readClipboard()") == "int main(){}"
    assert page.locator("#submission-source").text_content() == "int main(){}"
