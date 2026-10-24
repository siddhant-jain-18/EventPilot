"""Quote lifecycle: TTL expiry, capacity filtering, coordinator validation."""

from __future__ import annotations

import time

import pytest

from services.quote_service import CoordinatorQuoteService, SupplierQuoteService
from shared.errors import ValidationError
from shared.messages import QuoteLine, QuoteResponse
from storage.database import SUPPLIER_DDL, connect, init_db
from storage.supplier_repository import SupplierRepository


@pytest.fixture()
def supplier_repo(tmp_path):
    conn = connect(tmp_path / "supplier.db")
    init_db(conn, SUPPLIER_DDL)
    repo = SupplierRepository(conn)
    from shared.inventory import DEMO_PACKAGES

    for package in DEMO_PACKAGES:
        repo.seed_package(package.dict())
    return repo


def test_supplier_only_quotes_packages_that_fit(supplier_repo):
    service = SupplierQuoteService(supplier_repo, ttl_for_test=1800)
    quotes = service.create_quotes(
        event_id="E-1", plan_version=1, attendee_count=75, equipment_requirements=[]
    )
    ids = {q["package_id"] for q in quotes}
    assert "wp-starter-lab" not in ids  # capacity 60 < 75
    assert {"wp-standard-lab", "wp-premium-lab"} <= ids
    for quote in quotes:
        assert quote["quote_id"].startswith("Q-")
        assert quote["expires_at"] > quote["created_at"]


def test_quote_expiry_sweep(supplier_repo):
    service = SupplierQuoteService(supplier_repo, ttl_for_test=0)
    created = service.create_quotes(
        event_id="E-1", plan_version=1, attendee_count=60, equipment_requirements=[]
    )
    assert created
    time.sleep(0.02)
    expired = supplier_repo.expire_quotes(
        __import__("shared.time_utils", fromlist=["now_iso"]).now_iso()
    )
    assert expired >= 1
    for quote in created:
        assert supplier_repo.get_quote(quote["quote_id"])["status"] == "expired"


def _response(event_id="E-1", plan_version=1, request_id="REQ-1", tamper=None):
    line = QuoteLine(
        quote_id="Q-ABC123",
        package_id="wp-standard-lab",
        package_name="Standard Lab",
        capacity=80,
        attendee_count=60,
        price_inr_paise=1_920_000,
        deposit_fet_base=130_000_000_000_000_000,
        created_at="2026-10-24T06:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    if tamper:
        data = line.dict()
        data.update(tamper)
        line = QuoteLine(**data)
    return QuoteResponse(
        request_id=request_id,
        event_id=event_id,
        plan_version=plan_version,
        supplier_id="EventPilot-supplier-demo",
        quotes=[line],
    )


def _event():
    return {
        "event_id": "E-1",
        "owner_sender": "agent1qX",
        "current_plan_version": 1,
        "expected_attendees": 60,
    }


def test_coordinator_accepts_valid_response(supplier_repo, tmp_path):
    from storage.coordinator_repository import CoordinatorRepository
    from storage.database import COORDINATOR_DDL

    coord_repo = CoordinatorRepository(connect(tmp_path / "c.db"))
    init_db(coord_repo.conn, COORDINATOR_DDL)
    service = CoordinatorQuoteService(coord_repo)
    normalized = service.validate_response(
        _response().dict(),
        event=_event(),
        expected_request_id="REQ-1",
        attendee_count=60,
        plan_version=1,
    )
    assert len(normalized) == 1
    assert normalized[0]["price_inr_paise"] == 1_920_000


def test_coordinator_rejects_request_mismatch(supplier_repo, tmp_path):
    from storage.coordinator_repository import CoordinatorRepository
    from storage.database import COORDINATOR_DDL

    coord_repo = CoordinatorRepository(connect(tmp_path / "c2.db"))
    init_db(coord_repo.conn, COORDINATOR_DDL)
    service = CoordinatorQuoteService(coord_repo)
    with pytest.raises(ValidationError):
        service.validate_response(
            _response().dict(),
            event=_event(),
            expected_request_id="REQ-OTHER",
            attendee_count=60,
            plan_version=1,
        )


def test_coordinator_rejects_stale_plan_version(tmp_path):
    from storage.coordinator_repository import CoordinatorRepository
    from storage.database import COORDINATOR_DDL

    coord_repo = CoordinatorRepository(connect(tmp_path / "c3.db"))
    init_db(coord_repo.conn, COORDINATOR_DDL)
    service = CoordinatorQuoteService(coord_repo)
    with pytest.raises(ValidationError):
        service.validate_response(
            _response(plan_version=1).dict(),
            event=_event(),
            expected_request_id="REQ-1",
            attendee_count=60,
            plan_version=2,  # event moved on
        )


@pytest.mark.parametrize(
    "tamper",
    [
        {"package_id": "wp-does-not-exist"},
        {"price_inr_paise": 100},  # catalog mismatch
        {"capacity": 999},  # catalog mismatch
        {"attendee_count": 61},  # request mismatch
        {"deposit_fet_base": 1},  # catalog mismatch
    ],
)
def test_coordinator_rejects_tampered_lines(tmp_path, tamper):
    from storage.coordinator_repository import CoordinatorRepository
    from storage.database import COORDINATOR_DDL

    coord_repo = CoordinatorRepository(connect(tmp_path / "c4.db"))
    init_db(coord_repo.conn, COORDINATOR_DDL)
    service = CoordinatorQuoteService(coord_repo)
    with pytest.raises(ValidationError):
        service.validate_response(
            _response(tamper=tamper).dict(),
            event=_event(),
            expected_request_id="REQ-1",
            attendee_count=60,
            plan_version=1,
        )


def test_float_price_rejected(tmp_path):
    from storage.coordinator_repository import CoordinatorRepository
    from storage.database import COORDINATOR_DDL

    coord_repo = CoordinatorRepository(connect(tmp_path / "c5.db"))
    init_db(coord_repo.conn, COORDINATOR_DDL)
    service = CoordinatorQuoteService(coord_repo)
    raw = _response().dict()
    raw["quotes"][0]["price_inr_paise"] = 19200.50  # float money
    with pytest.raises(ValidationError):
        service.validate_response(
            raw,
            event=_event(),
            expected_request_id="REQ-1",
            attendee_count=60,
            plan_version=1,
        )
