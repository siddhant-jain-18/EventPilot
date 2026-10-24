"""FastAPI application factory for the RSVP service."""

from __future__ import annotations

from fastapi import FastAPI

from rsvp_web.routes import build_router
from shared.logging_config import setup_logging


def create_app(repos: dict, settings) -> FastAPI:
    """``repos`` must contain {"outreach": OutreachRepository}.

    The RSVP service shares the outreach database (WAL mode) so registrations
    recorded by the web service are immediately visible to the outreach agent.
    """
    setup_logging(settings.log_level)
    app = FastAPI(
        title="WorkshopPilot RSVP",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.include_router(build_router(repos, settings))
    return app


def app_from_env() -> FastAPI:
    from integrations.email_client import build_provider  # noqa: F401  (provider wiring lives with the agent)
    from rsvp_web.tokens import hash_code  # noqa: F401
    from storage.database import connect, init_db, OUTREACH_DDL
    from storage.outreach_repository import OutreachRepository
    from shared.config import load_settings

    settings = load_settings()
    conn = connect(settings.database_path("outreach.db"))
    init_db(conn, OUTREACH_DDL)
    return create_app({"outreach": OutreachRepository(conn)}, settings)
