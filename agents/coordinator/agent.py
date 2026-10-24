"""Coordinator agent entrypoint (ASI:One-facing).

Run:  python -m agents.coordinator.agent

AGENT_MAILBOX=true connects through the Agentverse mailbox relay so ASI:One
can reach this agent. SUPPLIER_ADDRESS / OUTREACH_ADDRESS must be set (see
scripts/print_addresses.py).
"""

from __future__ import annotations

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
        name="workshoppilot_coordinator",
        seed=seed,
        port=port,
        # None in mailbox mode: passing an endpoint would override the relay.
        endpoint=settings.agent_endpoints(8101),
        mailbox=settings.agent_mailbox,
        resolve=False,
        readme_path="README.md",
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
        try:
            (settings.database_path("coordinator.address")).write_text(agent.address)
        except OSError:
            pass

    # remember the latest chat sender so inter-agent responses can reach the user
    chat_proto = build_chat_protocol(service)
    agent.include(chat_proto, publish_manifest=True)
    agent.include(build_agent_protocol(service))
    return agent


agent = build_agent()

if __name__ == "__main__":
    agent.run()
