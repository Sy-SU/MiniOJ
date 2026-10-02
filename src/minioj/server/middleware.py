from __future__ import annotations

import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from minioj.config import settings
from minioj.server.errors import api_error_response

ACCOUNT_BODY_MAX_BYTES = 16 * 1024
JSON_BODY_OVERHEAD_BYTES = 64 * 1024
JSON_ESCAPE_EXPANSION = 6
ACCOUNT_PATHS = frozenset(
    {
        "/register",
        "/login",
        "/logout",
        "/settings/profile",
        "/settings/password",
        "/settings/tokens",
        "/api/v1/auth/register",
        "/api/v1/tokens",
    }
)
API_TESTCASE_PATH_RE = re.compile(r"^/api/v1/admin/problems/[^/]+/testcases(?:/\d+)?$")


def _api_body_limit(path: str, method: str) -> int | None:
    if method == "POST" and (
        path == "/api/v1/submissions"
        or re.fullmatch(r"/api/v1/contests/\d+/submissions", path)
    ):
        return (
            JSON_ESCAPE_EXPANSION * settings.source_limit_bytes
            + JSON_BODY_OVERHEAD_BYTES
        )
    if method == "POST" and path == "/api/v1/runs":
        return (
            JSON_ESCAPE_EXPANSION
            * (2 * settings.source_limit_bytes + settings.stdin_limit_bytes)
            + JSON_BODY_OVERHEAD_BYTES
        )
    if method in {"POST", "PUT"} and API_TESTCASE_PATH_RE.fullmatch(path):
        return (
            JSON_ESCAPE_EXPANSION * 2 * settings.testcase_file_limit_bytes
            + JSON_BODY_OVERHEAD_BYTES
        )
    return None


def _response(path: str, message: str, status_code: int) -> JSONResponse:
    if path == "/api/v1" or path.startswith("/api/v1/"):
        return api_error_response(status_code, message)
    return JSONResponse({"detail": message}, status_code=status_code)


class AccountBodyLimitMiddleware:
    """Bound sensitive bodies before JSON/form parsing, including chunked requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        path = path.rstrip("/")
        method = scope.get("method", "")
        is_token_delete = path.startswith("/settings/tokens/") and path.endswith(
            "/delete"
        )
        account_request = method == "POST" and (
            path in ACCOUNT_PATHS
            or is_token_delete
            or re.fullmatch(r"/(?:manage|admin)/users/\d+", path)
            or re.fullmatch(r"/manage/submissions/\d+/rejudge", path)
            or re.fullmatch(r"/contests/\d+/join", path)
        )
        limit = (
            ACCOUNT_BODY_MAX_BYTES if account_request else _api_body_limit(path, method)
        )
        if method == "POST" and re.fullmatch(
            r"/manage/contests/(?:new|\d+/edit)", path
        ):
            limit = 512 * 1024
        if limit is None:
            await self.app(scope, receive, send)
            return

        limit_label = (
            "Account request body must not exceed 16 KiB."
            if account_request
            else f"Request body must not exceed {limit} bytes."
        )
        too_large = _response(path, limit_label, 413)
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    await _response(path, "Invalid Content-Length.", 400)(
                        scope, receive, send
                    )
                    return
                if length < 0:
                    await _response(path, "Invalid Content-Length.", 400)(
                        scope, receive, send
                    )
                    return
                if length > limit:
                    await too_large(scope, receive, send)
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > limit:
                await too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> dict:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        await self.app(scope, replay, send)
