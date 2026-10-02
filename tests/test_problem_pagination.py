from __future__ import annotations

from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser

import pytest
import test_management_browser
from sqlalchemy import event
from test_phase4 import seed_user

from minioj.database import SessionLocal, engine
from minioj.models import Problem
from minioj.server.main import app

browser = test_management_browser.browser
chromium = test_management_browser.chromium


class ProblemIDs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href", "")
        if "/problems/paging-" in href:
            pid = href.rsplit("/", 1)[-1]
            if pid not in self.ids:
                self.ids.append(pid)


def seed_problems(count, *, ratings=None):
    uid, _ = seed_user("PagingOwner", role="system")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with SessionLocal() as db:
        db.add_all(
            [
                Problem(
                    id=f"paging-{i:03}",
                    title=f"Problem {i:03}",
                    statement="Public",
                    created_by=uid,
                    created_at=start + timedelta(seconds=i),
                    rating=ratings[i] if ratings else None,
                )
                for i in range(count)
            ]
        )
        db.add(
            Problem(
                id="paging-deleted",
                title="Removed",
                statement="PRIVATE",
                created_by=uid,
                deleted_at=start,
            )
        )
        db.commit()


@pytest.mark.parametrize("count", [0, 1, 49, 50, 51, 100, 101])
def test_web_and_api_pages_cover_all_problems_without_duplicates(client, count):
    seed_problems(count)
    web_seen, api_seen = [], []
    for page in range(1, 5):
        html = client.get(f"/problems?page={page}&page_size=1000")
        assert html.status_code == 200
        ids = ProblemIDs(html.text).ids
        response = client.get(f"/api/v1/problems?page={page}&page_size=1000")
        assert response.status_code == 200
        data = response.json()
        assert len(ids) <= 50 and len(data) <= 50
        assert response.headers["x-total-count"] == str(count)
        assert response.headers["x-page-size"] == "50"
        assert response.headers["x-total-pages"] == str(max(1, (count + 49) // 50))
        assert ids == [
            f"paging-{i:03}" for i in range((page - 1) * 50, min(page * 50, count))
        ]
        web_seen.extend(ids)
        api_seen.extend(p["problem_id"] for p in data)
    assert len(web_seen) == len(set(web_seen)) == count
    assert set(web_seen) == set(api_seen)
    # The old request remains an unpaginated JSON array, newest first.
    legacy = client.get("/api/v1/problems").json()
    assert [p["problem_id"] for p in legacy] == list(reversed(web_seen))


@pytest.mark.parametrize(
    "sort,expected",
    [
        ("default", [0, 1, 2, 3, 4, 5]),
        ("difficulty_asc", [0, 1, 2, 3, 4, 5]),
        ("difficulty_desc", [3, 1, 2, 0, 4, 5]),
    ],
)
def test_difficulty_sort_is_stable_and_null_last(client, sort, expected):
    seed_problems(6, ratings=[800, 1200, 1200, 2000, None, None])
    ids = [f"paging-{i:03}" for i in expected]
    assert ProblemIDs(client.get(f"/problems?sort={sort}").text).ids == ids
    api = client.get(f"/api/v1/problems?page=1&sort={sort}").json()
    assert [p["problem_id"] for p in api] == (
        list(reversed(ids)) if sort == "default" else ids
    )


@pytest.mark.parametrize("path", ["/problems", "/api/v1/problems"])
@pytest.mark.parametrize(
    "query", ["page=0", "page=-1", "page=no", "page=1.1", "sort=invalid"]
)
def test_invalid_queries_are_safe(client, path, query):
    response = client.get(path + "?" + query)
    assert response.status_code == 422
    assert "Traceback" not in response.text
    if path.startswith("/api"):
        assert response.json()["error"]["code"] == "validation_error"


def test_query_is_sql_bounded_search_escaped_and_huge_page_empty(client):
    seed_problems(101, ratings=[i % 4 * 400 for i in range(101)])
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        for path in ("/problems", "/api/v1/problems"):
            assert client.get(path + "?page=2&sort=difficulty_desc").status_code == 200
            assert client.get(path + "?page=" + "9" * 100).status_code == 200
        assert client.get("/api/v1/problems?page=1&q=%25").json() == []
        assert len(client.get("/api/v1/problems?page=1&q=Problem%2000").json()) == 10
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    loaded = [
        s for s in statements if "problems.statement" in s and "SELECT count(" not in s
    ]
    assert loaded and all("LIMIT" in s and "ORDER BY" in s for s in loaded)


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("width", [1365, 390])
@pytest.mark.parametrize(
    "browser",
    [{"java_script_enabled": True}, {"java_script_enabled": False}],
    indirect=True,
)
def test_real_browser_pagination_sort_and_no_js(
    browser, client, monkeypatch, prefix, width
):
    seed_problems(101, ratings=[None if i > 98 else i * 10 for i in range(101)])
    monkeypatch.setattr(app, "root_path", prefix)
    page = browser.new_page()
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(prefix + "/problems?filter=preserve")
    assert page.locator("tbody tr").count() == 50
    page.get_by_role("link", name="Next", exact=True).click()
    assert "page=2" in page.url and "filter=preserve" in page.url
    page.select_option("#problem-sort", "difficulty_desc")
    page.get_by_role("button", name="Apply", exact=True).click()
    assert "page=2" not in page.url
    assert page.locator("#problem-sort").input_value() == "difficulty_desc"
    assert (
        page.locator("tbody tr").first.locator("td").first.inner_text() == "paging-098"
    )
    page.get_by_role("link", name="Next", exact=True).click()
    assert "sort=difficulty_desc" in page.url and "page=2" in page.url
    assert page.locator("#problem-sort").input_value() == "difficulty_desc"
    page.get_by_role("link", name="Previous", exact=True).click()
    page.select_option("#problem-sort", "difficulty_asc")
    page.get_by_role("button", name="Apply", exact=True).click()
    assert (
        page.locator("tbody tr").first.locator("td").first.inner_text() == "paging-000"
    )
    page.get_by_role("link", name="Next", exact=True).click()
    page.get_by_role("link", name="Next", exact=True).click()
    assert page.locator("tbody tr").count() == 1
    assert page.locator("#problem-sort").input_value() == "difficulty_asc"
    page.get_by_role("link", name="Previous", exact=True).click()
    assert page.locator("tbody tr").count() == 50
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert "filter=preserve" in page.url
