"""Approval binding, single-use semantics, expiry and invalidation."""

from __future__ import annotations

import pytest

from services.approval_service import ApprovalService
from shared.errors import (
    AuthorizationError,
    DuplicateOperationError,
    ExpiredError,
    NotFoundError,
    ValidationError,
)
from shared.validation import canonical_hash
from storage.coordinator_repository import CoordinatorRepository
from storage.database import COORDINATOR_DDL, connect, init_db

OWNER = "agent1qOrganizer"


def make_approval_service(tmp_path):
    repo = CoordinatorRepository(connect(tmp_path / "coord.db"))
    init_db(repo.conn, COORDINATOR_DDL)
    return ApprovalService(repo, ttl_seconds=900), repo


def make_event(repo, owner=OWNER, plan_version=1):
    return repo.create_event(
        owner_sender=owner,
        title="Robotics Workshop",
        date="2026-11-15",
        city="Jaipur",
        expected_attendees=60,
        budget_inr_paise=2_000_000,
        current_plan_version=plan_version,
    )


def make_quote(event):
    return {
        "quote_id": "Q-TEST0001",
        "event_id": event["event_id"],
        "plan_version": event["current_plan_version"],
        "supplier_id": "workshoppilot-supplier-demo",
        "package_id": "wp-standard-lab",
        "package_name": "Standard Lab",
        "capacity": 80,
        "attendee_count": 60,
        "price_inr_paise": 1_920_000,
        "currency": "INR",
        "deposit_fet_base": 130_000_000_000_000_000,
        "inclusions": ["AV desk"],
        "status": "active",
        "created_at": "2026-10-24T06:00:00+00:00",
        "expires_at": "2026-10-24T06:30:00+00:00",
        "recipient_wallet": "wp-demo-supplier-wallet",
    }


def test_code_format_and_hash_binding(tmp_path):
    service, repo = make_approval_service(tmp_path)
    event = make_event(repo)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    assert approval["code"].startswith("A-") and len(approval["code"]) == 7
    assert approval["action_payload_hash"] == canonical_hash(approval["payload"])
    stored = repo.get_approval_by_code(approval["code"], OWNER)
    assert stored["status"] == "pending"
    assert stored["amount_inr_paise"] == 1_920_000


def test_consume_is_single_use(tmp_path):
    service, repo = make_approval_service(tmp_path)
    event = make_event(repo)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    consumed = service.consume(
        code_or_id=approval["code"],
        owner_sender=OWNER,
        event=event,
        expected_action=__import__("shared.enums", fromlist=["ActionType"]).ActionType.RESERVE_PACKAGE,
    )
    assert consumed["status"] == "consumed"
    with pytest.raises(DuplicateOperationError):
        service.consume(
            code_or_id=approval["code"],
            owner_sender=OWNER,
            event=event,
            expected_action=__import__("shared.enums", fromlist=["ActionType"]).ActionType.RESERVE_PACKAGE,
        )


def test_expired_approval_rejected(tmp_path):
    from datetime import datetime, timedelta, timezone

    class Clock:
        def __init__(self):
            self.dt = datetime.now(timezone.utc)

        def __call__(self):
            return self.dt

    clock = Clock()
    repo = CoordinatorRepository(connect(tmp_path / "coord2.db"))
    init_db(repo.conn, COORDINATOR_DDL)
    service = ApprovalService(repo, ttl_seconds=60)
    # patch time helpers through monkeypatching expiry generation
    event = make_event(repo)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    # simulate expiry by directly rewriting timestamps
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    repo.conn.execute(
        "UPDATE approvals SET expires_at = ? WHERE approval_id = ?",
        (past, approval["approval_id"]),
    )
    repo.conn.commit()
    from shared.enums import ActionType

    with pytest.raises(ExpiredError):
        service.consume(
            code_or_id=approval["code"],
            owner_sender=OWNER,
            event=event,
            expected_action=ActionType.RESERVE_PACKAGE,
        )


def test_stale_plan_version_rejected(tmp_path):
    from shared.enums import ActionType

    service, repo = make_approval_service(tmp_path)
    event = make_event(repo, plan_version=1)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    event_v2 = repo.update_event(event["event_id"], OWNER, current_plan_version=2)
    with pytest.raises(ValidationError) as err:
        service.consume(
            code_or_id=approval["code"],
            owner_sender=OWNER,
            event=event_v2,
            expected_action=ActionType.RESERVE_PACKAGE,
        )
    assert "stale" in str(err.value) or "plan version" in str(err.value)


def test_tampered_payload_detected(tmp_path):
    from shared.enums import ActionType

    service, repo = make_approval_service(tmp_path)
    event = make_event(repo)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    # tamper with the stored snapshot (price changed after approval)
    payload = dict(approval["payload"])
    payload["price_inr_paise"] = 1  # altered
    repo.conn.execute(
        "UPDATE approvals SET payload_json = ? WHERE approval_id = ?",
        (__import__("json").dumps(payload), approval["approval_id"]),
    )
    repo.conn.commit()
    with pytest.raises(AuthorizationError):
        service.consume(
            code_or_id=approval["code"],
            owner_sender=OWNER,
            event=event,
            expected_action=ActionType.RESERVE_PACKAGE,
        )


def test_owner_isolation(tmp_path):
    service, repo = make_approval_service(tmp_path)
    event = make_event(repo)
    approval = service.create_package_approval(event=event, quote=make_quote(event))
    other = make_event(repo, owner="agent1qSomeoneElse")
    with pytest.raises(NotFoundError):
        service.consume(
            code_or_id=approval["code"],
            owner_sender="agent1qSomeoneElse",
            event=other,
            expected_action=__import__("shared.enums", fromlist=["ActionType"]).ActionType.RESERVE_PACKAGE,
        )


def test_invalidate_pending(tmp_path):
    service, repo = make_approval_service(tmp_path)
    event = make_event(repo)
    service.create_package_approval(event=event, quote=make_quote(event))
    service.create_package_approval(event=event, quote=make_quote(event))
    invalidated = service.invalidate_pending_for_event(event["event_id"])
    assert invalidated == 2
    pending = repo.pending_approvals_for_owner(OWNER)
    assert pending == []
