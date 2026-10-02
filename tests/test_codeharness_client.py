from __future__ import annotations

import ast
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import smoke_test_codeharness_api as reference


class Clock:
    def __init__(self):
        self.now = 0
        self.delays = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


@pytest.fixture
def upstream():
    state = {
        "requests": [],
        "keys": [],
        "posts": 0,
        "polls": 0,
        "prefix": "",
        "disconnect": False,
        "never_finish": False,
        "mode": "diagnostic",
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            assert self.headers["Authorization"] == "Bearer opaque-test-token"
            state["requests"].append(self.path)
            parsed = urlsplit(self.path)
            prefix = state["prefix"] + "/api/v1"
            assert parsed.path.startswith(prefix)
            path = parsed.path[len(prefix) :]
            if path == "/me":
                self.send({"feedback_mode": state["mode"]})
            elif path == "/problems":
                page = int(parse_qs(parsed.query)["page"][0])
                assert "page_size" not in parse_qs(parsed.query)
                self.send(
                    [{"problem_id": f"other-{i}"} for i in range(50)]
                    if page == 1
                    else [{"problem_id": "demo-sum"}]
                )
            elif path == "/agent/problems/demo-sum":
                self.send({"problem_id": "demo-sum", "title": "Sum"})
            elif path == "/submissions/123":
                state["polls"] += 1
                finished = state["polls"] >= 3 and not state["never_finish"]
                self.send(
                    {
                        "status": "FINISHED" if finished else "RUNNING",
                        "verdict": "AC" if finished else None,
                        "tests": None,
                        "resources": None,
                        "finished_at": "time" if finished else None,
                        "summary": "WRONG! This summary must not control verdict",
                    }
                )
            elif path == "/agent/submissions/123/feedback":
                self.send(
                    {
                        "submission_id": 123,
                        "status": "FINISHED",
                        "verdict": "AC",
                        "feedback_mode": state["mode"],
                        "failed_test": None,
                        "compile": None,
                        "execution": None,
                        "diagnostic": None,
                        "summary": "WRONG! Not an AC",
                    }
                )
            else:
                self.send({"error": {"code": "not_found", "message": "secret"}}, 404)

        def do_POST(self):
            assert self.path == state["prefix"] + "/api/v1/submissions"
            state["posts"] += 1
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["keys"].append((self.headers["Idempotency-Key"], body))
            if state["disconnect"] and state["posts"] == 1:
                # Accepted before the response connection is lost.
                self.close_connection = True
                return
            self.send({"submission_id": 123, "status": "QUEUED"}, 202)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("prefix", ["", "/minioj"])
@pytest.mark.parametrize("mode", ["full", "diagnostic", "verdict_only"])
def test_client_only_uses_paginated_http_and_structured_state(
    upstream, monkeypatch, prefix, mode
):
    base, state = upstream
    state.update(prefix=prefix, mode=mode)
    clock = Clock()
    monkeypatch.setattr(reference, "time", clock)
    result = reference.run_flow(
        base + prefix + "/",
        "opaque-test-token",
        "demo-sum",
        idempotency_key="persistent-request-key",
    )
    assert result == {
        "submission_id": 123,
        "status": "FINISHED",
        "verdict": "AC",
        "feedback_mode": mode,
        "failed_test": None,
    }
    assert state["posts"] == 1
    assert clock.delays == [1, 2]
    assert any("page=2" in path for path in state["requests"])
    assert all(path.startswith(prefix + "/api/v1/") for path in state["requests"])


def test_transport_retry_keeps_same_key_payload_and_submission(upstream, monkeypatch):
    base, state = upstream
    state["disconnect"] = True
    monkeypatch.setattr(reference, "time", Clock())
    result = reference.run_flow(
        base, "opaque-test-token", "demo-sum", idempotency_key="retry-key"
    )
    assert result["submission_id"] == 123
    assert state["posts"] == 2 and state["keys"][0] == state["keys"][1]


def test_polling_has_finite_backoff_timeout_and_never_resubmits(upstream, monkeypatch):
    base, state = upstream
    state["never_finish"] = True
    clock = Clock()
    monkeypatch.setattr(reference, "time", clock)
    with pytest.raises(reference.ClientError, match="^client polling timeout$"):
        reference.run_flow(base, "opaque-test-token", "demo-sum", timeout=20)
    assert state["posts"] == 1
    assert clock.delays == [1, 2, 3, 5, 5, 4]


def test_client_has_no_internal_import_or_filesystem_dependency():
    code = Path(reference.__file__).read_text()
    tree = ast.parse(code)
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append(node.module or "")
    assert not any(n.startswith(("minioj", "sqlalchemy", "sqlite3")) for n in names)
    assert not any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id in {"open", "Path"}
        for n in ast.walk(tree)
    )
    assert not any(
        isinstance(n, ast.Subscript)
        and isinstance(n.slice, ast.Constant)
        and n.slice.value == "summary"
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/private",
        "http://user:secret@localhost",
        "http://localhost?token=secret",
        "http://localhost#fragment",
    ],
)
def test_client_refuses_unsafe_base_urls(url):
    with pytest.raises(reference.ClientError):
        reference.ReferenceClient(url, "opaque-test-token")
