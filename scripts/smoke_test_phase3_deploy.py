from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import selectors
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

AC_SOURCE = """#include <iostream>
int main() { long long a, b; if (std::cin >> a >> b) std::cout << a + b << '\\n'; }
"""
CE_SOURCE = "int main( {"
TLE_SOURCE = "int main() { for (;;) {} }"


@dataclass(frozen=True)
class HTTPResult:
    status: int
    body: bytes
    headers: object

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> dict:
        return json.loads(self.body)


class BrowserSession:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HTTPResult:
        request = urllib.request.Request(
            url, data=data, headers=headers or {}, method=method
        )
        try:
            response = self.opener.open(request, timeout=60)
        except urllib.error.HTTPError as exc:
            return HTTPResult(exc.code, exc.read(), exc.headers)
        with response:
            return HTTPResult(response.status, response.read(), response.headers)


def expect(result: HTTPResult, status: int, operation: str) -> HTTPResult:
    if result.status != status:
        raise RuntimeError(
            f"{operation} returned HTTP {result.status}: {result.text[:2000]}"
        )
    return result


def json_request(
    browser: BrowserSession,
    url: str,
    token: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
) -> HTTPResult:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    return browser.request(url, method=method, data=data, headers=headers)


def wait_for_http(url: str, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"HTTP service did not become ready: {last_error}")


def wait_for_worker(process: subprocess.Popen[str], timeout: float = 30) -> str:
    if process.stdout is None:
        raise RuntimeError("Worker stdout was not captured")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    output: list[str] = []
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output.extend(process.stdout.readlines())
            raise RuntimeError("Worker exited before ready:\n" + "".join(output))
        for key, _mask in selector.select(timeout=0.25):
            line = key.fileobj.readline()
            output.append(line)
            if "Worker ready" in line:
                selector.close()
                return "".join(output)
    selector.close()
    raise RuntimeError("Worker did not become ready:\n" + "".join(output))


def stop_worker(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def poll_submission(
    browser: BrowserSession, api_base: str, token: str, submission_id: int
) -> dict:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        result = expect(
            json_request(
                browser,
                f"{api_base}/submissions/{submission_id}",
                token,
            ),
            200,
            f"submission {submission_id} lookup",
        ).json()
        if result["status"] == "FINISHED":
            return result
        time.sleep(0.2)
    raise RuntimeError(f"Submission {submission_id} did not finish")


def browser_workflow(
    base: str, polygon_package: Path | None = None
) -> tuple[str, list[int]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is required; update the dev environment and run "
            "`python -m playwright install chromium`."
        ) from exc

    with sync_playwright() as playwright:
        chromium = playwright.chromium.launch(headless=True)
        page = chromium.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{base}/login")
        page.locator('input[name="identity"]').fill("phase3user")
        page.locator('input[name="password"]').fill("phase3-test-password")
        page.locator('form button[type="submit"]').click()
        page.wait_for_url(re.compile(r"/minioj/problems$"))

        page.goto(f"{base}/settings")
        page.locator('.inline-create input[name="name"]').fill("phase3-browser")
        page.locator('.inline-create button[type="submit"]').click()
        token_element = page.locator("#new-api-token")
        token_element.wait_for(state="visible")
        token = token_element.text_content()
        if not token:
            raise RuntimeError("Browser token creation returned no token")

        page.goto(f"{base}/problems/phase3-sum")
        editor = page.locator("#code-editor")
        editor.fill(AC_SOURCE)
        editor.evaluate("el => el.setSelectionRange(0, 0)")
        editor.press("Tab")
        if editor.input_value() != "    " + AC_SOURCE:
            raise RuntimeError("Code editor Tab did not indent")
        editor.press("Shift+Tab")
        if editor.input_value() != AC_SOURCE:
            raise RuntimeError("Code editor Shift+Tab altered the source")
        editor.press("Escape")
        page.keyboard.press("Tab")
        if not page.locator("#sample-select").evaluate(
            "el => el === document.activeElement"
        ):
            raise RuntimeError(
                "Code editor focus escape did not reach the sample selector"
            )
        page.locator("#run-sample-button").click()
        page.locator("#run-result").filter(has_text="Sample: output matches").wait_for(
            timeout=45_000
        )
        if "42" not in page.locator("#run-result").text_content():
            raise RuntimeError("Run Sample did not show the expected output")

        page.locator("#custom-input").fill("7 8\n")
        page.locator("#custom-test-button").click()
        page.locator("#run-result").filter(has_text="15").wait_for(timeout=45_000)

        page.locator("#submit-button").click()
        page.wait_for_url(re.compile(r"/minioj/submissions/\d+$"))
        page.locator("#submission-verdict").filter(has_text="AC").wait_for(
            timeout=45_000
        )
        page.locator(".detail-section").filter(has_text="Accepted").wait_for(
            timeout=10_000
        )
        source = page.locator("#submission-source")
        if (
            source.text_content() != AC_SOURCE
            or not source.locator(".syntax-keyword").count()
        ):
            raise RuntimeError("Source preview text/highlighting is incorrect")
        page.context.grant_permissions(["clipboard-read", "clipboard-write"])
        page.get_by_role("button", name="Copy code", exact=True).click()
        page.get_by_text("Source code copied.", exact=True).wait_for()
        if page.evaluate("navigator.clipboard.readText()") != AC_SOURCE:
            raise RuntimeError("Source copy did not preserve the complete code")
        match = re.search(r"/submissions/(\d+)$", page.url)
        if not match:
            raise RuntimeError(f"Could not read submission ID from {page.url}")
        submission_id = int(match.group(1))
        submission_ids = [submission_id]
        if polygon_package is not None:
            from minioj.polygon import parse_polygon

            package = parse_polygon(polygon_package.read_bytes())
            problem_id = package.values["id"]
            source = package.values["standard_source"]
            if not source:
                raise RuntimeError(
                    "Polygon deployment smoke requires a main C++ solution"
                )
            page.goto(f"{base}/manage")
            page.get_by_role("link", name="Import Polygon").click()
            page.locator('input[name="package_file"]').set_input_files(
                str(polygon_package)
            )
            page.get_by_role("button", name="Import problem").click()
            page.wait_for_url(
                f"{base}/manage/problems/{problem_id}/edit", timeout=120_000
            )
            from minioj.database import SessionLocal
            from minioj.models import Problem

            with SessionLocal() as db:
                imported = db.get(Problem, problem_id)
                if (
                    imported is None
                    or len(imported.testcases) != len(package.cases)
                    or len(imported.samples) != package.case_types.count("sample")
                ):
                    raise RuntimeError(
                        "Polygon browser import did not preserve test/sample counts"
                    )
            page.goto(f"{base}/problems/{problem_id}")
            page.wait_for_function(
                "Array.from(document.querySelectorAll('img[src*=\"/assets/\"]')).every(img => img.complete && img.naturalWidth > 0)"
            )
            if package.assets and page.locator('img[src*="/assets/"]').count() != len(
                package.assets
            ):
                raise RuntimeError("Polygon statement images are missing")
            page.locator("#code-editor").fill(source)
            page.locator("#run-sample-button").click()
            page.locator("#run-result").filter(
                has_text=(
                    "Sample: execution only"
                    if package.values["checker"] == "testlib"
                    else "Sample: output matches"
                )
            ).wait_for(timeout=45_000)
            page.locator("#submit-button").click()
            page.wait_for_url(re.compile(r"/minioj/submissions/\d+$"))
            page.wait_for_function(
                "!['QUEUED','COMPILING','RUNNING'].includes(document.querySelector('#submission-verdict').textContent.trim())",
                timeout=120_000,
            )
            if page.locator("#submission-verdict").text_content().strip() != "AC":
                from minioj.models import Submission

                failed_id = int(page.url.rsplit("/", 1)[1])
                with SessionLocal() as db:
                    result = json.loads(db.get(Submission, failed_id).judge_result)
                    raise RuntimeError(
                        f"Polygon main solution failed: {result['verdict']}; {result.get('summary')}; {result.get('resources')}"
                    )
            match = re.search(r"/submissions/(\d+)$", page.url)
            submission_ids.append(int(match.group(1)))
            print(
                f"Polygon browser upload passed: {problem_id}, {len(package.cases)} tests, {len(package.assets)} images; sample and full main solution AC."
            )
        if errors:
            raise RuntimeError(f"Browser script errors: {errors}")
        chromium.close()
    return token, submission_ids


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--polygon-package",
        type=Path,
        help="Additionally upload and judge a local complete Polygon ZIP",
    )
    parser.add_argument(
        "--server-image",
        help="Use a prebuilt Server image; skips the fresh image build",
    )
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    compose_file = project_root / "compose.yaml"
    worker_executable = Path(sys.executable).with_name("minioj-worker")
    if not worker_executable.is_file():
        raise SystemExit(f"Worker executable not found: {worker_executable}")

    with tempfile.TemporaryDirectory(prefix="minioj-phase3-deploy-") as temporary:
        root = Path(temporary)
        database_dir = root / "database"
        data_dir = root / "data"
        jobs_dir = data_dir / "jobs"
        database_dir.mkdir()
        data_dir.mkdir()
        port = free_port()
        direct_port = free_port()
        project_name = f"minioj-phase3-{os.getpid()}"
        owner = f"phase3-{os.getpid()}"
        secret = "phase3-isolated-deployment-secret-key"
        env_file = root / "compose.env"
        env_file.write_text(
            "\n".join(
                [
                    f"MINIOJ_SECRET_KEY={secret}",
                    "MINIOJ_HTTP_BIND=127.0.0.1",
                    f"MINIOJ_HTTP_PORT={port}",
                    f"MINIOJ_UID={os.getuid()}",
                    f"MINIOJ_GID={os.getgid()}",
                    f"MINIOJ_DATABASE_DIR={database_dir}",
                    f"MINIOJ_DATA_HOST_DIR={data_dir}",
                    "MINIOJ_FEEDBACK_POLICY=full",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        host_environment = {
            **os.environ,
            "MINIOJ_DATABASE_URL": f"sqlite:///{database_dir / 'oj.db'}",
            "MINIOJ_DATA_DIR": str(data_dir),
            "MINIOJ_JOB_DIR": str(jobs_dir),
            "MINIOJ_SECRET_KEY": secret,
            "MINIOJ_DOCKER_IMAGE": os.getenv(
                "MINIOJ_DOCKER_IMAGE", "minioj-cpp20:latest"
            ),
            "MINIOJ_WORKER_OWNER": owner,
        }
        os.environ.update(host_environment)

        from minioj.database import SessionLocal, init_db
        from minioj.models import Problem, Submission, User
        from minioj.problems import add_testcase
        from minioj.security import hash_password

        init_db()
        with SessionLocal() as db:
            user = User(
                username="phase3user",
                email="phase3@example.com",
                password_hash=hash_password("phase3-test-password"),
                role="admin" if args.polygon_package else "user",
            )
            db.add(user)
            db.flush()
            problem = Problem(
                id="phase3-sum",
                title="Phase 3 Sum",
                statement="Read two integers and print their sum.",
                input_specification="Two integers.",
                output_specification="Their sum.",
                time_limit_ms=1000,
                memory_limit_mb=128,
                created_by=user.id,
            )
            db.add(problem)
            db.commit()
            add_testcase(db, problem, "sample", "20 22\n", "42\n")
            add_testcase(db, problem, "hidden", "40 2\n", "42\n")

        compose = [
            "docker",
            "compose",
            "--file",
            str(compose_file),
            "--env-file",
            str(env_file),
            "--project-name",
            project_name,
        ]
        override = root / "smoke-override.yaml"
        override_lines = [
            "services:",
            "  server:",
            "    ports:",
            f"      - {json.dumps(f'127.0.0.1:{direct_port}:8000')}",
        ]
        if args.server_image:
            override_lines.append(f"    image: {json.dumps(args.server_image)}")
        override.write_text("\n".join(override_lines) + "\n", encoding="utf-8")
        compose.extend(["--file", str(override)])
        worker: subprocess.Popen[str] | None = None
        fault_alias = f"minioj-phase3-fault:{os.getpid()}"
        try:
            subprocess.run(
                [
                    *compose,
                    "up",
                    "--detach",
                    "--no-build" if args.server_image else "--build",
                    "--wait",
                ],
                cwd=project_root,
                check=True,
                timeout=600,
            )
            base = f"http://127.0.0.1:{port}/minioj"
            direct_base = f"http://127.0.0.1:{direct_port}"
            api_base = f"{base}/api/v1"
            wait_for_http(f"{base}/healthz")
            wait_for_http(f"{direct_base}/healthz")

            worker = subprocess.Popen(
                [str(worker_executable), "--poll-interval", "0.05"],
                cwd=project_root,
                env=host_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            wait_for_worker(worker)

            token, browser_submission_ids = browser_workflow(base, args.polygon_package)
            browser = BrowserSession()

            run = expect(
                json_request(
                    browser,
                    f"{api_base}/runs",
                    token,
                    method="POST",
                    payload={"source_code": AC_SOURCE, "stdin": "7 8\n"},
                ),
                200,
                "Custom Run",
            ).json()
            if run["status"] != "OK" or run["stdout"] != "15\n":
                raise RuntimeError(f"Unexpected Custom Run result: {run}")
            with SessionLocal() as db:
                submission_ids_in_db = [
                    row.id for row in db.query(Submission).order_by(Submission.id)
                ]
                if submission_ids_in_db != browser_submission_ids:
                    raise RuntimeError("Custom Run created a formal Submission")

            expected_verdicts = ((CE_SOURCE, "CE"), (TLE_SOURCE, "TLE"))
            for source, verdict in expected_verdicts:
                queued = expect(
                    json_request(
                        browser,
                        f"{api_base}/submissions",
                        token,
                        method="POST",
                        payload={
                            "problem_id": "phase3-sum",
                            "language": "cpp20",
                            "source_code": source,
                        },
                    ),
                    202,
                    f"{verdict} submission",
                ).json()
                result = poll_submission(
                    browser, api_base, token, queued["submission_id"]
                )
                if result["verdict"] != verdict:
                    raise RuntimeError(f"Expected {verdict}, received {result}")

            independent_client = project_root / "scripts" / "smoke_test_phase4_api.py"
            for label, client_base in (
                ("proxy subpath", base),
                ("direct root path", direct_base),
            ):
                subprocess.run(
                    [sys.executable, str(independent_client)],
                    cwd=project_root,
                    env={
                        **os.environ,
                        "OJ_BASE_URL": client_base,
                        "OJ_API_TOKEN": token,
                        "OJ_PROBLEM_ID": "phase3-sum",
                    },
                    check=True,
                    timeout=180,
                )
                print(f"Phase 4 independent client passed through {label}.")
                subprocess.run(
                    [
                        sys.executable,
                        str(project_root / "scripts" / "smoke_test_codeharness_api.py"),
                        "--expect-verdict",
                        "AC",
                    ],
                    cwd=project_root,
                    env={
                        **os.environ,
                        "OJ_BASE_URL": client_base,
                        "OJ_API_TOKEN": token,
                        "OJ_PROBLEM_ID": "phase3-sum",
                    },
                    check=True,
                    timeout=180,
                )
                print(f"Phase 5 reference client passed through {label}.")

            stop_worker(worker)
            worker = None
            subprocess.run(
                ["docker", "tag", host_environment["MINIOJ_DOCKER_IMAGE"], fault_alias],
                check=True,
            )
            fault_environment = {
                **host_environment,
                "MINIOJ_DOCKER_IMAGE": fault_alias,
            }
            worker = subprocess.Popen(
                [str(worker_executable), "--poll-interval", "0.05"],
                cwd=project_root,
                env=fault_environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            wait_for_worker(worker)
            subprocess.run(["docker", "image", "rm", fault_alias], check=True)
            fault = json_request(
                browser,
                f"{api_base}/runs",
                token,
                method="POST",
                payload={"source_code": AC_SOURCE, "stdin": "1 2\n"},
            )
            if fault.status != 503:
                raise RuntimeError(
                    f"Docker fault returned HTTP {fault.status}: {fault.text}"
                )
            expect(browser.request(f"{base}/healthz"), 200, "health after Docker fault")

            containers = subprocess.run(
                [
                    "docker",
                    "ps",
                    "--all",
                    "--quiet",
                    "--filter",
                    f"label=minioj.owner={owner}",
                ],
                text=True,
                capture_output=True,
                check=True,
            ).stdout.split()
            if containers:
                raise RuntimeError(f"Worker containers remain: {containers}")
            leftovers = [
                path.name for path in jobs_dir.iterdir() if path.name != ".worker.lock"
            ]
            if leftovers:
                raise RuntimeError(f"Worker job directories remain: {leftovers}")

            print(
                "Phase 3 deployment smoke passed: /minioj/ login and token creation; "
                "Worker-backed Custom Run; AC/CE/TLE submissions; root and proxy-subpath "
                "independent API clients; Docker-fault 503; cleanup."
            )
        except Exception:
            logs = subprocess.run(
                [*compose, "logs", "--no-color", "--tail", "100"],
                cwd=project_root,
                text=True,
                capture_output=True,
                check=False,
            )
            if logs.stdout:
                print(logs.stdout, file=sys.stderr)
            raise
        finally:
            stop_worker(worker)
            subprocess.run(
                ["docker", "image", "rm", fault_alias],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                [*compose, "down", "--remove-orphans", "--rmi", "local"],
                cwd=project_root,
                check=False,
                timeout=60,
            )


if __name__ == "__main__":
    main()
