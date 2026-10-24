"""Supplier agent entrypoint.

Run:  python -m agents.supplier.agent
"""

from __future__ import annotations

from uagents import Agent

from agents.supplier.handlers import build_protocol
from agents.supplier.service import SupplierService
from payments.ledger import SimulatedChain
from payments.verification import SimulatedVerifier
from services.quote_service import SupplierQuoteService
from shared.config import load_settings
from shared.logging_config import setup_logging
from shared.messages import QuoteResponse  # noqa: F401  (wire contracts)
from storage.database import connect, init_db
from storage.supplier_repository import SupplierRepository
from storage.database import SUPPLIER_DDL


def build_agent() -> Agent:
    settings = load_settings()
    setup_logging(settings.log_level)
    problems = settings.validate()
    if problems:
        raise SystemExit("Configuration problems:\n- " + "\n- ".join(problems))

    conn = connect(settings.database_path("supplier.db"))
    init_db(conn, SUPPLIER_DDL)
    repo = SupplierRepository(conn)

    chain = SimulatedChain(settings.database_path("simulated_chain.jsonl"))
    verifier = SimulatedVerifier(chain=chain, consume_tx_id=repo.consume_tx_id)
    quote_service = SupplierQuoteService(repo, ttl_seconds=settings.quote_ttl_seconds)
    service = SupplierService(
        repo,
        quote_service,
        verifier,
        supplier_wallet_address=settings.supplier_wallet_address,
        payment_network=settings.payment_network if settings.payment_mode == "testnet" else "simulated",
        audit=None,
    )

    seed = settings.agent_seed or settings.supplier_seed
    if not seed:
        raise SystemExit("SUPPLIER_SEED is required to start the supplier agent")
    agent = Agent(
        name="workshoppilot_supplier",
        seed=seed,
        port=settings.agent_port or 8102,
        endpoint=settings.agent_endpoints(8102),
        mailbox=settings.agent_mailbox,
        resolve=False,
    )

    @agent.on_event("startup")
    async def announce(ctx):
        ctx.logger.info("supplier agent up at %s (demo inventory)", agent.address)
        try:
            (settings.database_path("supplier.address")).write_text(agent.address)
        except OSError:
            pass

    agent.include(build_protocol(service))
    return agent


agent = build_agent()

if __name__ == "__main__":
    agent.run()
