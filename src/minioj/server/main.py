from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from minioj.accounts import create_administrator
from minioj.config import settings
from minioj.database import init_db
from minioj.server.api import router as api_router
from minioj.server.middleware import AccountBodyLimitMiddleware
from minioj.server.web import router as web_router


def _bootstrap_admin() -> None:
    username = os.getenv("MINIOJ_ADMIN_USERNAME")
    email = os.getenv("MINIOJ_ADMIN_EMAIL")
    password = os.getenv("MINIOJ_ADMIN_PASSWORD")
    if not any((username, email, password)):
        return
    if not all((username, email, password)):
        raise RuntimeError(
            "Set all three MINIOJ_ADMIN_USERNAME, MINIOJ_ADMIN_EMAIL and MINIOJ_ADMIN_PASSWORD values."
        )
    try:
        create_administrator(username, email, password, allow_existing=True)
    except ValueError as exc:
        raise RuntimeError(f"Administrator initialization failed: {exc}") from exc


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    _bootstrap_admin()
    yield


settings.validate_server()
app = FastAPI(
    title="MiniOJ",
    version="0.1.0",
    description="A lightweight online judge with a stable API for coding agents.",
    lifespan=lifespan,
    root_path=settings.root_path,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="minioj_session",
    same_site="lax",
    https_only=settings.session_https_only,
    max_age=60 * 60 * 24 * 14,
)
app.add_middleware(AccountBodyLimitMiddleware)
app.mount(
    "/static",
    StaticFiles(directory=str(settings.static_dir)),
    name="static",
)
app.include_router(api_router)
app.include_router(web_router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict:
    return {"status": "ok"}
