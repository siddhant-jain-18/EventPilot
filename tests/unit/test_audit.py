"""Audit trail: append-only records, sanitization, narration."""

from __future__ import annotations

from services.audit_service import AuditService
from shared.enums import Actor
from storage.coordinator_repository import CoordinatorRepository
from storage.database import COORDINATOR_DDL, connect, init_db


def _repo(tmp_path):
    repo = CoordinatorRepository(connect(tmp_path / "audit.db"))
    init_db(repo.conn, COORDINATOR_DDL)
    return repo


def test_records_and_timeline(tmp_path):
    repo = _repo(tmp_path)
    audit = AuditService(repo)
    audit.record(
        actor=Actor.ORGANIZER.value,
        action="event_created",
        entity_type="event",
        entity_id="E-1",
        event_id="E-1",
        metadata={"attendees": 60},
    )
    audit.record(
        actor=Actor.SUPPLIER.value,
        action="quotes_received",
        entity_type="quote",
        entity_id="Q-1",
        event_id="E-1",
    )
    entries = audit.timeline("E-1")
    assert [e["action"] for e in entries] == ["event_created", "quotes_received"]
    text = audit.timeline_narration("E-1")
    assert "event_created" in text and "quotes_received" in text


def test_metadata_sanitized(tmp_path):
    repo = _repo(tmp_path)
    audit = AuditService(repo)
    audit.record(
        actor=Actor.COORDINATOR.value,
        action="invite",
        entity_type="invitation",
        entity_id="I-1",
        event_id="E-1",
        metadata={
            "email": "alice@example.test",
            "body": "secret body",
            "api_key": "super-secret-key-123",
            "amount_inr": 1_920_000,
        },
    )
    raw = repo.conn.execute("SELECT safe_metadata FROM audit_log").fetchone()[0]
    assert "alice@example.test" not in raw
    assert "secret body" not in raw
    assert "super-secret-key-123" not in raw
    assert "1920000" in raw


def test_timeline_isolated_per_event(tmp_path):
    repo = _repo(tmp_path)
    audit = AuditService(repo)
    audit.record(actor="system", action="a1", entity_type="event", entity_id="E-1", event_id="E-1")
    audit.record(actor="system", action="a2", entity_type="event", entity_id="E-2", event_id="E-2")
    assert [e["action"] for e in audit.timeline("E-1")] == ["a1"]
    assert [e["action"] for e in audit.timeline("E-2")] == ["a2"]
