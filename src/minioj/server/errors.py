from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, PlainTextResponse, Response

from minioj.feedback import safe_diagnostic

ERROR_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}


def is_api_request(request: Request) -> bool:
    path = request.scope.get("path", "")
    root_path = request.scope.get("root_path", "").rstrip("/")
    if root_path and path.startswith(root_path + "/"):
        path = path[len(root_path) :]
    return path == "/api/v1" or path.startswith("/api/v1/")


def _message(status_code: int, detail: object) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict):
        for key in ("message", "summary"):
            value = detail.get(key)
            if isinstance(value, str):
                return value
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request failed"


def api_error_payload(
    status_code: int,
    detail: str | dict[str, Any] | list[dict[str, Any]],
    *,
    code: str | None = None,
    details: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, object]:
    """Build the Phase-4 error envelope without dropping legacy ``detail``."""

    message = _message(status_code, detail)
    if details is None and isinstance(detail, (dict, list)):
        details = detail
    return {
        "error": {
            "code": code or ERROR_CODES.get(status_code, "request_failed"),
            "message": message,
            "details": details,
        },
        # Kept for existing browser code and pre-Phase-4 clients. New clients use error.
        "detail": detail,
    }


def api_error_response(
    status_code: int,
    detail: str | dict[str, Any] | list[dict[str, Any]],
    *,
    code: str | None = None,
    details: dict[str, Any] | list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        api_error_payload(status_code, detail, code=code, details=details),
        status_code=status_code,
        headers=headers,
    )


async def api_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    if not is_api_request(request):
        return await http_exception_handler(request, exc)
    return api_error_response(
        exc.status_code,
        _safe_error_detail(exc.detail),
        code=getattr(exc, "error_code", None),
        headers=dict(exc.headers) if exc.headers else None,
    )


def _safe_error_detail(detail):
    if isinstance(detail, str):
        return safe_diagnostic(detail)[0]
    if isinstance(detail, dict):
        return {
            key: _safe_error_detail(value)
            for key, value in detail.items()
            if key in {"status", "summary", "message", "location", "type"}
        }
    if isinstance(detail, list):
        return [_safe_error_detail(value) for value in detail]
    return detail if detail is None or type(detail) in {int, bool} else None


async def api_internal_exception_handler(request: Request, exc: Exception) -> Response:
    # No exception text, SQL, credentials or source in this ordinary error log/body.
    logging.getLogger("minioj.api").error(
        "Unhandled request failure (%s)", type(exc).__name__
    )
    if is_api_request(request):
        return api_error_response(500, "Internal server error")
    return PlainTextResponse("Internal Server Error", status_code=500)


async def api_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    if not is_api_request(request):
        return await request_validation_exception_handler(request, exc)
    issues = [
        {
            "location": list(error.get("loc", ())),
            "message": safe_diagnostic(error.get("msg", "Invalid value"))[0],
            "type": error.get("type", "value_error"),
        }
        for error in exc.errors()
    ]
    return api_error_response(
        422,
        issues,
        code="validation_error",
        details=issues,
    )
