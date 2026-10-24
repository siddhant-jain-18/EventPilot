"""Shared fixtures: a full in-memory harness for all three agent services
plus a message bus that stands in for the uAgents transport.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agents.coordinator.service import CoordinatorService
from agents.outreach.service import OutreachService
from agents.supplier.service import SupplierService
from integrations.email_client import ConsoleEmailProvider
from payments.ledger import SimulatedChain
from payments.verification import SimulatedVerifier
from services.approval_service import ApprovalService
from services.audit_service import AuditService
from services.checklist_service import ChecklistService
from services.quote_service import CoordinatorQuoteService, SupplierQuoteService
from services.replanning_service import ReplanningService
from shared.config import Settings
from shared.inventory import DEMO_PACKAGES
from storage.coordinator_repository import CoordinatorRepository
from storage.database import (
    COORDINATOR_DDL,
    OUTREACH_DDL,
    SUPPLIER_DDL,
    connect,
    init_db,
)
from storage.outreach_repository import OutreachRepository
from storage.supplier_repository import SupplierRepository

COORDINATOR_WALLET = "wp-demo-coordinator-wallet"
SUPPLIER_WALLET = "wp-demo-supplier-wallet"
ALLOWLIST = ["alice@example.test", "bob@example.test"]


class Clock:
    """Injectable clock so expiry behaviour is deterministic."""

    def __init__(self):
        self.dt = datetime.now(timezone.utc)

    def __call__(self) -> datetime:
        return self.dt

    def advance(self, seconds: float) -> None:
        self.dt = self.dt + timedelta(seconds=seconds)


def make_settings(tmp_path, **overrides) -> Settings:
    base = dict(
        app_env="test",
        database_dir=str(tmp_path),
        quote_ttl_seconds=1800,
        approval_ttl_seconds=900,
        agent_reply_timeout_seconds=30.0,
        rate_limit_per_minute=1000,
        payment_mode="simulated",
        payment_network="simulated",
        supplier_wallet_address=SUPPLIER_WALLET,
        coordinator_wallet_address=COORDINATOR_WALLET,
        email_provider="console",
        allowed_test_recipients=list(ALLOWLIST),
        rsvp_signing_secret="unit-test-secret",
        rsvp_base_url="http://127.0.0.1:8104",
        rsvp_token_ttl_seconds=7 * 24 * 3600,
        simulated_coordinator_balance_fet="1.0",
        max_transfer_fet="0.2",
        per_event_max_fet="0.6",
    )
    base.update(overrides)
    return Settings(**base)


def build_harness(tmp_path, *, quote_ttl: int = 1800, approval_ttl: int = 900, clock: Clock | None = None, **extra):
    settings = make_settings(
        tmp_path, quote_ttl_seconds=quote_ttl, approval_ttl_seconds=approval_ttl, **extra
    )
    clock = clock or Clock()

    # --- supplier -----------------------------------------------------------
    supplier_conn = connect(settings.database_path("supplier.db"))
    init_db(supplier_conn, SUPPLIER_DDL)
    supplier_repo = SupplierRepository(supplier_conn)
    for package in DEMO_PACKAGES:
        supplier_repo.seed_package(package.dict())

    chain = SimulatedChain(settings.database_path("simulated_chain.jsonl"))
    chain.open_account(COORDINATOR_WALLET, settings.simulated_coordinator_balance_fet)
    verifier = SimulatedVerifier(chain=chain, consume_tx_id=supplier_repo.consume_tx_id)
    supplier = SupplierService(
        supplier_repo,
        SupplierQuoteService(supplier_repo, ttl_seconds=quote_ttl),
        verifier,
        supplier_wallet_address=SUPPLIER_WALLET,
        payment_network="simulated",
        audit=AuditService(supplier_repo),
    )

    # --- outreach -----------------------------------------------------------
    outreach_conn = connect(settings.database_path("outreach.db"))
    init_db(outreach_conn, OUTREACH_DDL)
    outreach_repo = OutreachRepository(outreach_conn)
    provider = ConsoleEmailProvider()
    outreach = OutreachService(
        outreach_repo,
        settings,
        provider,
        audit=AuditService(outreach_repo),
    )

    # --- coordinator ----------------------------------------------------------
    coord_conn = connect(settings.database_path("coordinator.db"))
    init_db(coord_conn, COORDINATOR_DDL)
    coord_repo = CoordinatorRepository(coord_conn)
    audit = AuditService(coord_repo)
    approvals = ApprovalService(coord_repo, ttl_seconds=approval_ttl)
    coordinator = CoordinatorService(
        coord_repo,
        approvals,
        CoordinatorQuoteService(coord_repo),
        ReplanningService(coord_repo, approvals),
        ChecklistService(coord_repo),
        audit,
        settings,
        chain=chain,
        llm=None,
        now_fn=clock,
    )
    return SimpleNamespace(
        settings=settings,
        clock=clock,
        chain=chain,
        supplier=supplier,
        supplier_repo=supplier_repo,
        outreach=outreach,
        outreach_repo=outreach_repo,
        coordinator=coordinator,
        coordinator_repo=coord_repo,
        approvals=approvals,
        audit=audit,
    )


class Bus:
    """Routes Outbound messages between the three services until quiescent.

    Mirrors what the uAgents handler layer does with ctx.send; it also enforces
    the no-message-loops rule by refusing to exceed a hop budget.
    """

    def __init__(self, harness):
        self.h = harness
        self.log: list = []

    def deliver(self, turn) -> list:
        """Route one turn's outgoing messages; returns ALL user-facing replies
        (including replies produced by peer responses)."""
        replies = list(turn.replies)
        queue = list(turn.outgoing)
        hops = 0
        from shared.messages import (
            InvitationDraftResponse,
            PaymentStatusResponse,
            QuoteResponse,
            ReservationResponse,
            ReservationSupersedeResponse,
            RsvpCountResponse,
            RsvpSubmitResponse,
            SendInvitationResponse,
        )

        supplier_response_types = (
            QuoteResponse,
            ReservationResponse,
            ReservationSupersedeResponse,
            PaymentStatusResponse,
        )
        outreach_response_types = (
            InvitationDraftResponse,
            SendInvitationResponse,
            RsvpCountResponse,
            RsvpSubmitResponse,
        )
        while queue:
            hops += 1
            if hops > 60:
                raise AssertionError("bus exceeded hop budget: possible message loop")
            out = queue.pop(0)
            self.log.append(out)
            if out.role == "supplier":
                res = self.h.supplier.handle(out.msg)
                queue.extend(res.outgoing)
            elif out.role == "outreach":
                res = self.h.outreach.handle(out.msg)
                queue.extend(res.outgoing)
            elif out.role == "coordinator":
                msg = out.msg
                if isinstance(msg, supplier_response_types):
                    res = self.h.coordinator.handle_supplier_message(msg)
                elif isinstance(msg, outreach_response_types):
                    res = self.h.coordinator.handle_outreach_message(msg)
                else:
                    raise AssertionError(f"unroutable coordinator message {type(msg).__name__}")
                replies.extend(res.replies)
                queue.extend(res.outgoing)
            else:
                raise AssertionError(f"unknown outbound role {out.role}")
        return replies

    def turn(self, sender: str, text: str) -> list:
        """One user turn fully routed. Returns the user-facing replies."""
        return self.deliver(self.h.coordinator.handle_user_text(sender, text))

    @staticmethod
    def joined(replies) -> str:
        return "\n".join(r.text for r in replies)


def extract_code(text: str) -> str:
    match = re.search(r"\b([A-Z]-[A-Z2-9]{5})\b", text)
    assert match, f"no approval code found in: {text[:300]}"
    return match.group(1)


@pytest.fixture()
def harness(tmp_path):
    return build_harness(tmp_path)


@pytest.fixture()
def bus(harness):
    return Bus(harness)
