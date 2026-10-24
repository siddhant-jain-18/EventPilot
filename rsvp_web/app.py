"""FastAPI application factory for the RSVP service."""

from __future__ import annotations

import time
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from rsvp_web.routes import build_router
from shared.logging_config import setup_logging


class ProductionHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("x-request-id") or uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["referrer-policy"] = "no-referrer"
        response.headers["cache-control"] = "no-store"
        response.headers["server-timing"] = f"app;dur={(time.perf_counter() - started) * 1000:.1f}"
        return response


def create_app(repos: dict, settings) -> FastAPI:
    """``repos`` must contain {"outreach": OutreachRepository}.

    The RSVP service shares the outreach database (WAL mode) so registrations
    recorded by the web service are immediately visible to the outreach agent.
    """
    setup_logging(settings.log_level)
    app = FastAPI(
        title="EventPilot RSVP",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(ProductionHeadersMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.include_router(build_router(repos, settings))
    return app


def app_from_env() -> FastAPI:
    from integrations.email_client import build_provider  # noqa: F401  (provider wiring lives with the agent)
    from rsvp_web.tokens import hash_code  # noqa: F401
    from storage.database import connect, init_db, OUTREACH_DDL
    from storage.outreach_repository import OutreachRepository
    from shared.config import load_settings

    settings = load_settings()
    problems = settings.validate()
    if problems:
        raise RuntimeError("Configuration problems:\n- " + "\n- ".join(problems))
    conn = connect(settings.database_path("outreach.db"))
    init_db(conn, OUTREACH_DDL)
    return create_app({"outreach": OutreachRepository(conn)}, settings)
