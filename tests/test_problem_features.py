from __future__ import annotations

import re

from minioj.database import SessionLocal
from minioj.models import Problem, User


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def create_admin_session(client) -> int:
    token = csrf_from(client.get("/register").text)
    response = client.post(
        "/register",
        data={
            "csrf_token": token,
            "username": "MdAdmin",
            "email": "md-admin@example.com",
            "password": "long-enough-password",
            "password_confirmation": "long-enough-password",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with SessionLocal() as db:
        user = db.query(User).filter_by(username="MdAdmin").one()
        user.role = "admin"
        db.commit()
        return user.id


def problem_form_data(token: str, problem_id: str) -> dict[str, str]:
    return {
        "csrf_token": token,
        "id": problem_id,
        "title": "Mixed Case ID",
        "statement": "# Statement",
        "input_specification": "Input",
        "output_specification": "Output",
        "notes": "",
        "time_limit_ms": "1000",
        "memory_limit_mb": "128",
        "rating": "",
        "source": "",
        "source_id": "",
        "source_url": "",
        "tags": "",
    }


def problem_api_data(problem_id: str) -> dict[str, object]:
    return {
        "id": problem_id,
        "title": "API Mixed Case ID",
        "statement": "# Statement",
        "input_specification": "Input",
        "output_specification": "Output",
        "notes": "",
        "time_limit_ms": 1000,
        "memory_limit_mb": 128,
        "tags": "",
    }


def test_mixed_case_problem_ids_work_in_web_and_api(client):
    create_admin_session(client)
    token = csrf_from(client.get("/admin/problems/new").text)

    created = client.post(
        "/admin/problems/new",
        data=problem_form_data(token, "AbC123"),
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/admin/problems/AbC123/edit"
    assert client.get("/problems/AbC123").status_code == 200

    api_created = client.post(
        "/api/v1/admin/problems",
        headers={"X-CSRF-Token": token},
        json=problem_api_data("XYZ789"),
    )
    assert api_created.status_code == 201
    assert api_created.json()["problem_id"] == "XYZ789"


def test_problem_markdown_is_rendered_and_embedded_html_is_escaped(client):
    admin_id = create_admin_session(client)
    with SessionLocal() as db:
        db.add(
            Problem(
                id="MdCase1",
                title="Markdown problem",
                statement=(
                    "# Main heading\n\n"
                    "Use **bold**, `code`, and $a_i \\le k$.\n\n"
                    "$$\\max_i a_i - \\min_i a_i$$\n\n"
                    '<script id="md-xss">bad</script>'
                ),
                input_specification="| value | meaning |\n| --- | --- |\n| 1 | one |",
                output_specification="```text\nanswer\n```",
                notes="[unsafe](javascript:alert(1))",
                created_by=admin_id,
            )
        )
        db.commit()

    page = client.get("/problems/MdCase1")
    assert page.status_code == 200
    assert "<h1>Main heading</h1>" in page.text
    assert "<strong>bold</strong>" in page.text
    assert "<code>code</code>" in page.text
    assert '<span class="math inline">a_i \\le k</span>' in page.text
    assert '<div class="math block">\n\\max_i a_i - \\min_i a_i\n</div>' in page.text
    assert "<table>" in page.text
    assert "<pre><code" in page.text
    assert '<script id="md-xss">' not in page.text
    assert "&lt;script id=&quot;md-xss&quot;&gt;" in page.text
    assert 'href="javascript:' not in page.text
    assert "/static/vendor/katex/katex.min.css" in page.text
    assert "/static/vendor/katex/katex.min.js" in page.text
    assert "/static/math.js" in page.text

    assert client.get("/static/vendor/katex/katex.min.css").status_code == 200
    assert client.get("/static/vendor/katex/katex.min.js").status_code == 200
    assert (
        client.get("/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2").status_code
        == 200
    )


def test_problem_editor_can_preview_the_same_safe_markdown(client):
    create_admin_session(client)
    editor = client.get("/admin/problems/new")
    token = csrf_from(editor.text)
    assert 'id="markdown-preview-button"' in editor.text
    assert "/static/problem_form.js" in editor.text
    assert "/static/vendor/katex/katex.min.js" in editor.text
    assert "/static/math.js" in editor.text

    preview = client.post(
        "/admin/problems/preview",
        data={
            "csrf_token": token,
            "statement": "## Preview heading\n\n**previewed** with $x^2$",
            "input_specification": "`n` integers",
            "output_specification": "",
            "notes": "<img src=x onerror=alert(1)>",
        },
    )
    assert preview.status_code == 200
    assert "<h2>Preview heading</h2>" in preview.text
    assert "<strong>previewed</strong>" in preview.text
    assert '<span class="math inline">x^2</span>' in preview.text
    assert "<code>n</code>" in preview.text
    assert "<img src=x" not in preview.text
    assert "&lt;img src=x onerror=alert(1)&gt;" in preview.text
