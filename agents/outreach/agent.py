"""Outreach agent entrypoint.

Run:  python -m agents.outreach.agent
"""

from __future__ import annotations

from uagents import Agent

from agents.outreach.handlers import build_protocol
from agents.outreach.service import OutreachService
from integrations.email_client import build_provider
from storage.database import OUTREACH_DDL, connect, init_db
from storage.outreach_repository import OutreachRepository
from shared.config import load_settings
from shared.logging_config import setup_logging


def build_agent() -> Agent:
    settings = load_settings()
    setup_logging(settings.log_level)
    problems = settings.validate()
    if problems:
        raise SystemExit("Configuration problems:\n- " + "\n- ".join(problems))

    conn = connect(settings.database_path("outreach.db"))
    init_db(conn, OUTREACH_DDL)
    repo = OutreachRepository(conn)
    provider = build_provider(settings)
    service = OutreachService(repo, settings, provider)

    seed = settings.agent_seed or settings.outreach_seed
    if not seed:
        raise SystemExit("OUTREACH_SEED is required to start the outreach agent")
    agent = Agent(
        name="workshoppilot_outreach",
        seed=seed,
        port=settings.agent_port or 8103,
        endpoint=settings.agent_endpoints(8103),
        mailbox=settings.agent_mailbox,
        resolve=False,
    )

    @agent.on_event("startup")
    async def announce(ctx):
        ctx.logger.info(
            "outreach agent up at %s (email provider: %s; allowlisted recipients: %d)",
            agent.address,
            settings.email_provider,
            len(settings.allowed_test_recipients),
        )
        try:
            (settings.database_path("outreach.address")).write_text(agent.address)
        except OSError:
            pass

    agent.include(build_protocol(service))
    return agent


agent = build_agent()

if __name__ == "__main__":
    agent.run()
