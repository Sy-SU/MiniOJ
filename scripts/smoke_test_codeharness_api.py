"""Standalone reference client: standard library, HTTP, Bearer and JSON only."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

SOURCE = """#include <iostream>
int main() { long long a, b; if (std::cin >> a >> b) std::cout << a + b << '\\n'; }
"""
MODES = {"full", "diagnostic", "verdict_only"}
STATES = {"QUEUED", "COMPILING", "RUNNING", "FINISHED"}
VERDICTS = {"AC", "WA", "CE", "RE", "TLE", "MLE", "OLE", "IE"}
DELAYS = (1, 2, 3, 5)
MAX_HTTP_BYTES = 32 * 1024 * 1024


class ClientError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward a Bearer secret to an unrelated redirected origin.
        return None


class ReferenceClient:
    def __init__(self, base_url: str, token: str, *, request_timeout=15):
        url = urllib.parse.urlsplit(base_url)
        if (
            url.scheme not in {"http", "https"}
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ClientError(
                "base URL must be an HTTP(S) installation URL without credentials, query or fragment"
            )
        if request_timeout <= 0:
            raise ClientError("request timeout must be positive")
        self.api_base = base_url.rstrip("/") + "/api/v1"
        self.token = token
        self.request_timeout = request_timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, path, *, payload=None, key=None, timeout=None):
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        request = urllib.request.Request(
            self.api_base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=headers,
            method=method,
        )
        try:
            response = self.opener.open(
                request, timeout=timeout or self.request_timeout
            )
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(MAX_HTTP_BYTES + 1)
            if len(raw) > MAX_HTTP_BYTES:
                raise ClientError(
                    "HTTP response exceeds the reference client's byte limit"
                )
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ClientError(f"HTTP {response.code}: expected JSON") from exc
            if response.code not in {200, 202}:
                code = (
                    body.get("error", {}).get("code", "unknown")
                    if isinstance(body, dict)
                    else "unknown"
                )
                # Never print raw error bodies, URLs, tokens, source or natural-language diagnostics.
                if code not in {
                    "authentication_required",
                    "forbidden",
                    "not_found",
                    "validation_error",
                    "rate_limited",
                    "conflict",
                    "idempotency_conflict",
                    "service_unavailable",
                    "internal_error",
                    "payload_too_large",
                }:
                    code = "unknown"
                raise ClientError(f"HTTP {response.code}: {code}")
            return body

    def discover(self, problem_id, *, sort="default"):
        seen = set()
        page = 1
        while True:
            rows = self.request(
                "GET",
                "/problems?" + urllib.parse.urlencode({"page": page, "sort": sort}),
            )
            if not isinstance(rows, list) or len(rows) > 50:
                raise ClientError("invalid paginated problem list")
            ids = [row.get("problem_id") for row in rows if isinstance(row, dict)]
            if (
                len(ids) != len(rows)
                or any(not isinstance(pid, str) or pid in seen for pid in ids)
                or len(set(ids)) != len(ids)
            ):
                raise ClientError("duplicate or invalid problem discovery page")
            if problem_id in ids:
                return
            seen.update(ids)
            if len(rows) < 50:
                raise ClientError("requested problem was not found")
            page += 1

    def create(self, payload, key):
        # Transport failures can mean the server committed. Every retry has the same key/body.
        for attempt in range(3):
            try:
                return self.request("POST", "/submissions", payload=payload, key=key)
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ):
                if attempt == 2:
                    raise ClientError(
                        "submission transport timeout; retry explicitly with the same idempotency key"
                    ) from None
                time.sleep(DELAYS[attempt])

    def poll(self, submission_id, *, timeout):
        if timeout <= 0:
            raise ClientError("polling timeout must be positive")
        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ClientError("client polling timeout")
            try:
                body = self.request(
                    "GET",
                    f"/submissions/{submission_id}",
                    timeout=min(remaining, self.request_timeout),
                )
            except (
                urllib.error.URLError,
                TimeoutError,
                ConnectionError,
                http.client.HTTPException,
            ):
                if time.monotonic() >= deadline:
                    raise ClientError("client polling timeout") from None
                body = None
            if body is not None:
                if not isinstance(body, dict) or body.get("status") not in STATES:
                    raise ClientError("invalid structured submission state")
                if body["status"] == "FINISHED":
                    if body.get("verdict") not in VERDICTS:
                        raise ClientError("finished submission has no valid verdict")
                    return body
                if any(
                    body.get(key) is not None
                    for key in ("verdict", "tests", "resources", "finished_at")
                ):
                    raise ClientError("pending submission contains final result fields")
            delay = min(
                DELAYS[min(attempt, len(DELAYS) - 1)],
                max(0, deadline - time.monotonic()),
            )
            time.sleep(delay)
            attempt += 1


def run_flow(
    base_url,
    token,
    problem_id,
    *,
    source_code=SOURCE,
    timeout=60,
    request_timeout=15,
    idempotency_key=None,
    sort="default",
):
    client = ReferenceClient(base_url, token, request_timeout=request_timeout)
    identity = client.request("GET", "/me")
    mode = identity.get("feedback_mode") if isinstance(identity, dict) else None
    if mode not in MODES:
        raise ClientError("invalid feedback_mode")
    client.discover(problem_id, sort=sort)
    problem = client.request(
        "GET", "/agent/problems/" + urllib.parse.quote(problem_id, safe="")
    )
    if (
        not isinstance(problem, dict)
        or problem.get("problem_id") != problem_id
        or {"rating", "tags", "editorial", "testcases", "standard_source"}
        & problem.keys()
    ):
        raise ClientError("invalid sanitized problem response")
    created = client.create(
        {"problem_id": problem_id, "language": "cpp20", "source_code": source_code},
        idempotency_key or str(uuid.uuid4()),
    )
    sid = created.get("submission_id") if isinstance(created, dict) else None
    if type(sid) is not int or sid < 1 or created.get("status") != "QUEUED":
        raise ClientError("invalid submission acceptance response")
    submission = client.poll(sid, timeout=timeout)
    feedback = client.request("GET", f"/agent/submissions/{sid}/feedback")
    required = {
        "submission_id",
        "status",
        "verdict",
        "feedback_mode",
        "failed_test",
        "compile",
        "execution",
        "diagnostic",
    }
    if (
        not isinstance(feedback, dict)
        or not required <= feedback.keys()
        or feedback["submission_id"] != sid
        or feedback["status"] != "FINISHED"
        or feedback["verdict"] != submission["verdict"]
        or feedback["feedback_mode"] != mode
    ):
        raise ClientError("invalid structured feedback response")
    return {
        "submission_id": sid,
        "status": feedback["status"],
        "verdict": feedback["verdict"],
        "feedback_mode": mode,
        "failed_test": feedback["failed_test"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("OJ_BASE_URL"))
    parser.add_argument(
        "--token",
        default=os.getenv("OJ_API_TOKEN"),
        help="Prefer OJ_API_TOKEN to avoid secrets in process arguments",
    )
    parser.add_argument(
        "--problem-id", default=os.getenv("OJ_PROBLEM_ID", "phase5-sum")
    )
    parser.add_argument(
        "--source-code",
        default=os.getenv("OJ_SOURCE_CODE", SOURCE),
        help="Default is the demo two-integer sum solution",
    )
    parser.add_argument(
        "--timeout", type=float, default=60, help="Total polling timeout, seconds"
    )
    parser.add_argument("--request-timeout", type=float, default=15)
    parser.add_argument("--idempotency-key", default=os.getenv("OJ_IDEMPOTENCY_KEY"))
    parser.add_argument(
        "--sort",
        choices=["default", "difficulty_asc", "difficulty_desc"],
        default="default",
    )
    parser.add_argument("--expect-verdict", choices=sorted(VERDICTS), default=None)
    args = parser.parse_args()
    if (
        not args.base_url
        or not args.token
        or args.timeout <= 0
        or args.request_timeout <= 0
    ):
        parser.error("base URL/token and positive timeouts are required")
    key = args.idempotency_key or str(uuid.uuid4())
    print(f"Idempotency-Key: {key}", flush=True)
    try:
        result = run_flow(
            args.base_url,
            args.token,
            args.problem_id,
            source_code=args.source_code,
            timeout=args.timeout,
            request_timeout=args.request_timeout,
            idempotency_key=key,
            sort=args.sort,
        )
        print(json.dumps(result), flush=True)
        if args.expect_verdict and result["verdict"] != args.expect_verdict:
            raise ClientError("unexpected structured verdict")
    except ClientError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
