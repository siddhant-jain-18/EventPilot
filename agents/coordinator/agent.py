"""Coordinator agent entrypoint (ASI:One-facing).

Run:  python -m agents.coordinator.agent

AGENT_MAILBOX=true connects through the Agentverse mailbox relay so ASI:One
can reach this agent. SUPPLIER_ADDRESS / OUTREACH_ADDRESS must be set (see
scripts/print_addresses.py).
"""

from __future__ import annotations

import httpx
from uagents import Agent

from agents.coordinator.handlers import build_agent_protocol, build_chat_protocol
from agents.coordinator.service import CoordinatorService
from payments.ledger import PaymentCaps, SimulatedChain
from services.approval_service import ApprovalService
from services.audit_service import AuditService
from services.checklist_service import ChecklistService
from services.quote_service import CoordinatorQuoteService
from services.replanning_service import ReplanningService
from shared.config import load_settings
from shared.landing import add_status_page
from shared.logging_config import setup_logging
from storage.coordinator_repository import CoordinatorRepository
from storage.database import COORDINATOR_DDL, connect, init_db


def build_agent() -> Agent:
    settings = load_settings()
    setup_logging(settings.log_level)
    problems = settings.validate()
    if problems:
        raise SystemExit("Configuration problems:\n- " + "\n- ".join(problems))
    if not settings.supplier_address or not settings.outreach_address:
        raise SystemExit(
            "SUPPLIER_ADDRESS and OUTREACH_ADDRESS are required (run scripts/print_addresses.py)"
        )

    conn = connect(settings.database_path("coordinator.db"))
    init_db(conn, COORDINATOR_DDL)
    repo = CoordinatorRepository(conn)
    audit = AuditService(repo)
    approvals = ApprovalService(repo, ttl_seconds=settings.approval_ttl_seconds)
    quote_service = CoordinatorQuoteService(repo)
    replanning = ReplanningService(repo, approvals)
    checklists = ChecklistService(repo)

    chain = SimulatedChain(settings.database_path("simulated_chain.jsonl"))
    chain.open_account(
        settings.coordinator_wallet_address, settings.simulated_coordinator_balance_fet
    )

    llm = None
    if settings.llm_enabled:
        from integrations.asi1_client import ASI1Client

        llm = ASI1Client(
            api_key=settings.asi1_api_key,
            base_url=settings.asi1_base_url,
            model=settings.asi1_model,
            timeout_seconds=settings.asi1_timeout_seconds,
        )

    service = CoordinatorService(
        repo,
        approvals,
        quote_service,
        replanning,
        checklists,
        audit,
        settings,
        chain=chain,
        llm=llm,
    )

    seed = settings.agent_seed or settings.coordinator_seed
    if not seed:
        raise SystemExit("COORDINATOR_SEED is required to start the coordinator agent")
    port = settings.agent_port or 8101
    agent = Agent(
        name="eventpilot_coordinator",
        seed=seed,
        port=port,
        # None in mailbox mode: passing an endpoint would override the relay.
        endpoint=settings.agent_endpoints(8101),
        mailbox=settings.agent_mailbox,
        resolve=False,
        readme_path="README.md",
    )

    add_status_page(
        agent,
        service="eventpilot_coordinator",
        hint=(
            "This port speaks the uAgents protocol; it is not a web app. Chat with the "
            "coordinator in ASI:One or the Agentverse Inspector, or run "
            "`python -m scripts.chat_cli` in a second terminal."
        ),
        links={"agent_info": "/agent_info", "submit": "/submit", "asi_one": "https://asi1.ai/"},
    )

    @agent.on_event("startup")
    async def announce(ctx):
        ctx.logger.info(
            "coordinator agent up at %s (payment mode: %s; email provider: %s; LLM: %s)",
            agent.address,
            settings.payment_mode,
            settings.email_provider,
            "ASI1" if llm else "deterministic-only",
        )
        if settings.agentverse_api_key:
            await _register_agentverse(ctx, settings, agent.address)
        if settings.agent_endpoint:
            # uAgents always prints an inspector URL for the local listener.
            # For a hosted deployment the browser cannot reach 127.0.0.1, so
            # print the inspector URL for the public endpoint as well.
            from urllib.parse import quote

            public_base = settings.agent_endpoint.rstrip("/")
            if public_base.endswith("/submit"):
                public_base = public_base[: -len("/submit")]
            ctx.logger.info(
                "Agentverse public inspector (manual registration may still be needed): https://agentverse.ai/inspect/?uri=%s&address=%s",
                quote(public_base, safe=""),
                agent.address,
            )
        try:
            (settings.database_path("coordinator.address")).write_text(agent.address)
        except OSError:
            pass

    # remember the latest chat sender so inter-agent responses can reach the user
    chat_proto = build_chat_protocol(service)
    agent.include(chat_proto, publish_manifest=True)
    agent.include(build_agent_protocol(service))
    return agent


async def _register_agentverse(ctx, settings, address: str) -> None:
    """Register/update the external-agent listing when explicitly enabled.

    Agentverse's API moved to a challenge/response flow: POST /v1/auth/challenge
    (with the agent address) returns a JWT; the registration POST to /v1/agents
    must include that challenge plus a signed challenge_response. The coordinator
    only has the API key, not the agent private key, so it cannot complete the
    signed challenge. This function therefore intentionally skips the v1
    challenge-based registration and logs the exact reason so the deploy logs
    are informative instead of noisy.

    The coordinator is still published through uAgents' built-in Almanac/API
    registration (visible in the logs just above this line) and reaches ASI:One
    via the Agentverse mailbox relay when AGENT_MAILBOX=true, or via the public
    endpoint when AGENT_ENDPOINT is set. A manual one-shot registration that
    holds the agent key is available in scripts/register_agentverse.py.
    """
    endpoint = settings.agent_endpoint
    if settings.agent_mailbox or not endpoint:
        ctx.logger.warning(
            "Agentverse API registration skipped: requires AGENT_MAILBOX=false "
            "and AGENT_ENDPOINT"
        )
        return
    if not settings.agentverse_api_key:
        ctx.logger.warning(
            "Agentverse API registration skipped: AGENTVERSE_API_KEY not set"
        )
        return
    ctx.logger.info(
        "Agentverse API registration skipped: v1 API requires a signed "
        "challenge/response that the coordinator cannot produce without the agent "
        "private key. Almanac/API registration + mailbox relay / public endpoint "
        "provide the visible listing. To register anyway, run "
        "scripts/register_agentverse.py with the agent key on a trusted host."
    )
    return


agent = build_agent()

if __name__ == "__main__":
    agent.run()
