from __future__ import annotations

import io
import stat
import sys
import zipfile
from dataclasses import replace

import pytest
from sqlalchemy import select
from test_web import csrf_from

from minioj import polygon, problems
from minioj.config import settings
from minioj.database import SessionLocal
from minioj.judge.checker import outputs_match
from minioj.models import Problem, Sample, User
from minioj.models import TestCase as CaseModel
from minioj.polygon import import_polygon, parse_polygon
from minioj.problems import testcase_contents as read_case
from minioj.security import hash_password
from minioj.server import web
from minioj.server.main import app


@pytest.fixture(autouse=True)
def isolated_import_files(tmp_path, monkeypatch):
    local = replace(settings, data_dir=tmp_path / "data")
    for module in (polygon, problems, web, sys.modules[__name__]):
        monkeypatch.setattr(module, "settings", local)


XML = """<problem short-name="polygon-sum" url="https://polygon.codeforces.com/example">
<names><name language="english" value="Polygon Sum"/></names>
<statements><statement language="english" type="text/html" path="statements/english/problem.html"/></statements>
<judging input-file="" output-file=""><testset name="tests"><time-limit>1000</time-limit>
<memory-limit>134217728</memory-limit><test-count>2</test-count>
<input-path-pattern>tests/%02d</input-path-pattern><answer-path-pattern>tests/%02d.a</answer-path-pattern>
<tests><test sample="true" method="manual"/><test method="generated"/></tests>
</testset></judging><assets><checker name="std::wcmp.cpp"/>
<solutions><solution tag="main"><source type="cpp.g++17" path="solutions/main.cpp"/></solution></solutions>
</assets></problem>"""
HTML = """<html><head><script>badHead()</script></head><body>
<div class="legend"><p>Print <b>$$$a+b$$$</b>.</p><img src="plot.png"><script>badScript()</script></div>
<div class="input-specification"><div class="section-title">Input</div><p>Two integers.</p></div>
<div class="output-specification"><p>The sum.</p></div><div class="note"><p>Notes.</p></div>
</body></html>"""


def package(*, xml=XML, html=HTML, extra=None, omit=(), prefix=""):
    entries = {
        "problem.xml": xml.encode(),
        "statements/english/problem.html": html.encode(),
        "statements/english/plot.png": b"\x89PNG\r\n\x1a\nimage",
        "solutions/main.cpp": b"int main() {}",
        "tests/01": b"1 2\n",
        "tests/01.a": b"3\n",
        "tests/02": b"PRIVATE_HIDDEN_INPUT",
        "tests/02.a": b"PRIVATE_HIDDEN_ANSWER",
    }
    entries.update(extra or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            if name not in omit:
                archive.writestr(prefix + name, value)
    return stream.getvalue()


def admin_session(client, *, role="admin"):
    with SessionLocal() as db:
        user = User(
            username="polygonadmin",
            email="polygon@example.com",
            role=role,
            password_hash=hash_password("polygon-test-password"),
        )
        db.add(user)
        db.commit()
    client.post(
        "/login",
        data={
            "csrf_token": csrf_from(client.get("/login").text),
            "identity": "polygonadmin",
            "password": "polygon-test-password",
        },
    )
    return csrf_from(client.get("/settings").text)


@pytest.mark.parametrize("prefix", ["", "exported-problem/"])
def test_import_preserves_metadata_cases_images_and_source(client, prefix):
    csrf = admin_session(client)
    page = client.get("/admin")
    assert "/manage/problems/import" in page.text
    assert 'type="file"' in client.get("/admin/problems/import").text
    response = client.post(
        "/admin/problems/import",
        data={"csrf_token": csrf},
        files={
            "package_file": ("polygon.zip", package(prefix=prefix), "application/zip")
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/admin/problems/polygon-sum/edit"
    with SessionLocal() as db:
        problem = db.get(Problem, "polygon-sum")
        assert (
            problem.title,
            problem.time_limit_ms,
            problem.memory_limit_mb,
            problem.checker,
        ) == ("Polygon Sum", 1000, 128, "testlib")
        assert problem.checker_name == "std::wcmp.cpp"
        assert problem.checker_bundle and problem.checker_sha256
        assert problem.standard_source == "int main() {}"
        assert problem.standard_sha256
        assert "badScript" not in problem.statement
        assert "badHead" not in problem.statement
        assert "**$a+b$**" in problem.statement
        cases = db.scalars(
            select(CaseModel)
            .where(CaseModel.problem_id == problem.id)
            .order_by(CaseModel.order)
        ).all()
        assert [case.type for case in cases] == ["sample", "generated"]
        assert read_case(cases[1]) == (
            "PRIVATE_HIDDEN_INPUT",
            "PRIVATE_HIDDEN_ANSWER",
        )
        sample = db.scalar(select(Sample).where(Sample.problem_id == problem.id))
        assert sample.input == "1 2\n" and sample.testcase_id == cases[0].id
        assert all(case.input_sha256 and case.output_sha256 for case in cases)
    client.post("/logout", data={"csrf_token": csrf})
    public = client.get("/problems/polygon-sum")
    assert public.status_code == 200
    assert "This problem has been modified" not in public.text
    assert "alert-warning" not in public.text
    assert "PRIVATE_HIDDEN" not in public.text
    assert "int main()" not in public.text
    filename = next((settings.problems_dir / "polygon-sum" / "assets").iterdir()).name
    image = client.get(f"/problems/polygon-sum/assets/{filename}")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get("/problems/polygon-sum/assets/02.a").status_code == 404


@pytest.mark.parametrize(
    "bad_package",
    [
        package(extra={"../escape": b"bad"}),
        package(extra={"/absolute": b"bad"}),
        package(extra={"another/problem.xml": XML.encode()}),
        package(omit=("tests/02.a",)),
        package(xml=XML.replace("std::wcmp.cpp", "custom.cpp")),
        package(xml=XML.replace('input-file=""', 'input-file="input.txt"')),
        package(
            xml=XML.replace(
                "<problem ", '<!DOCTYPE problem [<!ENTITY secret "text">]><problem '
            )
        ),
        package(xml=XML.replace("<test-count>2", "<test-count>3")),
        package(xml=XML.replace("tests/%02d", "../tests/%02d")),
        package(html=HTML.replace("plot.png", "https://example.com/image.png")),
        package(extra={"statements/english/plot.png": b"<svg onload='bad()'>"}),
        package(extra={"tests/02": b"\xff\xfe"}),
        b"not a zip",
    ],
)
def test_invalid_package_leaves_no_database_rows(bad_package):
    with pytest.raises(ValueError):
        parse_polygon(bad_package)
    with SessionLocal() as db:
        assert db.scalar(select(Problem)) is None


def test_symlink_and_duplicate_archive_entries_are_rejected():
    data = io.BytesIO(package())
    with zipfile.ZipFile(data, "a") as archive:
        link = zipfile.ZipInfo("symlink")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../../outside")
    with pytest.raises(ValueError, match="unsafe"):
        parse_polygon(data.getvalue())
    data = io.BytesIO(package())
    with pytest.warns(UserWarning), zipfile.ZipFile(data, "a") as archive:
        archive.writestr("tests/01", "changed")
    with pytest.raises(ValueError, match="duplicate"):
        parse_polygon(data.getvalue())


def test_archive_expanded_and_per_file_limits(monkeypatch):
    monkeypatch.setattr(
        polygon, "settings", replace(settings, polygon_expanded_limit_bytes=100)
    )
    with pytest.raises(ValueError, match="Expanded"):
        parse_polygon(package())


def test_upload_limit_is_applied_during_multipart_parsing(client, monkeypatch):
    csrf = admin_session(client)
    monkeypatch.setattr(
        web, "settings", replace(settings, polygon_archive_limit_bytes=100)
    )
    response = client.post(
        "/admin/problems/import",
        data={"csrf_token": csrf},
        files={"package_file": ("p.zip", package())},
    )
    assert response.status_code == 413
    with SessionLocal() as db:
        assert db.scalar(select(Problem)) is None


def test_import_requires_admin_and_csrf(client):
    assert (
        client.get("/admin/problems/import", follow_redirects=False).status_code == 303
    )
    csrf = admin_session(client, role="user")
    assert (
        client.post(
            "/admin/problems/import",
            data={"csrf_token": csrf},
            files={"package_file": ("p.zip", package())},
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        db.query(User).one().role = "admin"
        db.commit()
    assert (
        client.post(
            "/admin/problems/import", files={"package_file": ("p.zip", package())}
        ).status_code
        == 403
    )


def test_existing_problem_is_never_overwritten(client):
    csrf = admin_session(client)
    for attempt in range(2):
        response = client.post(
            "/admin/problems/import",
            data={"csrf_token": csrf},
            files={"package_file": ("p.zip", package())},
            follow_redirects=False,
        )
        assert response.status_code == (303 if attempt == 0 else 422)
    with SessionLocal() as db:
        assert len(db.scalars(select(Problem)).all()) == 1
        assert len(db.scalars(select(CaseModel)).all()) == 2


def test_statement_asset_uses_request_root_path(client, monkeypatch):
    csrf = admin_session(client)
    response = client.post(
        "/admin/problems/import",
        data={"csrf_token": csrf},
        files={"package_file": ("p.zip", package())},
    )
    assert response.status_code == 200
    monkeypatch.setattr(app, "root_path", "/minioj")
    page = client.get("/problems/polygon-sum")
    assert 'src="/minioj/problems/polygon-sum/assets/' in page.text
    filename = next((settings.problems_dir / "polygon-sum" / "assets").iterdir()).name
    assert (
        client.get(f"/minioj/problems/polygon-sum/assets/{filename}").status_code == 200
    )
    with SessionLocal() as db:
        from minioj.models import utcnow

        db.get(Problem, "polygon-sum").deleted_at = utcnow()
        db.commit()
    assert (
        client.get(f"/minioj/problems/polygon-sum/assets/{filename}").status_code == 404
    )


def test_commit_failure_removes_all_import_files(client, monkeypatch):
    admin_session(client)
    parsed = parse_polygon(package(), "rollback-problem")
    with SessionLocal() as db:
        admin = db.scalar(select(User))

        def fail():
            raise RuntimeError("commit failed")

        monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(RuntimeError, match="commit failed"):
            import_polygon(db, parsed, admin.id)
    with SessionLocal() as db:
        assert db.get(Problem, "rollback-problem") is None
        assert not db.scalars(select(CaseModel)).all()
    assert not (settings.problems_dir / "rollback-problem").exists()


@pytest.mark.parametrize(
    "actual,expected,result",
    [
        ("yEs NO", "Yes\nNo\n", True),
        ("YES", "Yes No", False),
        ("Maybe", "Maybe", False),
        ("no", "yes", False),
    ],
)
def test_polygon_yesno_checker(actual, expected, result):
    assert outputs_match(actual, expected, "yesno") is result
