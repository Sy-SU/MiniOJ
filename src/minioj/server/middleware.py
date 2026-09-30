from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

ACCOUNT_BODY_MAX_BYTES = 16 * 1024
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


class AccountBodyLimitMiddleware:
    """Bound account requests before JSON/form parsing, including chunked bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        root = scope.get("root_path", "").rstrip("/")
        if root and path.startswith(root + "/"):
            path = path[len(root) :]
        path = path.rstrip("/")
        is_token_delete = path.startswith("/settings/tokens/") and path.endswith(
            "/delete"
        )
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or (path not in ACCOUNT_PATHS and not is_token_delete)
        ):
            await self.app(scope, receive, send)
            return

        too_large = JSONResponse(
            {"detail": "Account request body must not exceed 16 KiB."}, status_code=413
        )
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    await JSONResponse(
                        {"detail": "Invalid Content-Length."}, status_code=400
                    )(scope, receive, send)
                    return
                if length > ACCOUNT_BODY_MAX_BYTES:
                    await too_large(scope, receive, send)
                    return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > ACCOUNT_BODY_MAX_BYTES:
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
