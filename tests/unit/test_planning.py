"""Deterministic planning rules for the 60/75-attendee scenarios (spec 9)."""

from __future__ import annotations

import pytest

from shared.errors import BudgetExceededError, CapacityExceededError, ValidationError
from shared.inventory import DEMO_PACKAGES, PACKAGE_DEPOSITS_ATFET
from services.planning import (
    committed_plus_proposed_within_budget,
    evaluate_packages,
    validate_selection,
)

BUDGET = 2_000_000


def _packages():
    return [p.dict() for p in DEMO_PACKAGES]


def test_60_attendees_starter_and_standard_fit_premium_blocked():
    quotes = {
        p["package_id"]: {
            "quote_id": f"Q-{p['package_id']}",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for p in _packages()
    }
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=BUDGET,
        required_equipment=["projector"],
        packages=_packages(),
        quotes_by_package=quotes,
    )
    by_id = {c.package_id: c for c in checks}
    assert by_id["wp-starter-lab"].eligible
    assert by_id["wp-standard-lab"].eligible
    assert by_id["wp-standard-lab"].headroom_inr_paise == 80_000
    assert not by_id["wp-premium-lab"].eligible
    assert by_id["wp-premium-lab"].reasons == ["over_budget"]


def test_75_attendees_starter_capacity_fail_standard_fits():
    quotes = {
        p["package_id"]: {
            "quote_id": f"Q-{p['package_id']}",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for p in _packages()
    }
    checks = evaluate_packages(
        attendee_count=75,
        budget_inr_paise=BUDGET,
        required_equipment=["projector"],
        packages=_packages(),
        quotes_by_package=quotes,
    )
    by_id = {c.package_id: c for c in checks}
    assert by_id["wp-starter-lab"].reasons == ["capacity_insufficient"]
    assert by_id["wp-standard-lab"].eligible
    assert by_id["wp-standard-lab"].headroom_inr_paise == 80_000
    assert by_id["wp-premium-lab"].reasons == ["over_budget"]


def test_budget_boundary_equality_is_allowed():
    quotes = {
        p["package_id"]: {
            "quote_id": "Q-1",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for p in _packages()
    }
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=1_450_000,  # exactly the Starter Lab price
        required_equipment=[],
        packages=_packages(),
        quotes_by_package=quotes,
    )
    by_id = {c.package_id: c for c in checks}
    assert by_id["wp-starter-lab"].eligible
    assert by_id["wp-starter-lab"].headroom_inr_paise == 0


def test_equipment_requirement_filters_packages():
    quotes = {
        p["package_id"]: {
            "quote_id": "Q-1",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for p in _packages()
    }
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=10_000_000,
        required_equipment=["recording_setup"],
        packages=_packages(),
        quotes_by_package=quotes,
    )
    by_id = {c.package_id: c for c in checks}
    assert "equipment_missing" in by_id["wp-starter-lab"].reasons
    assert "equipment_missing" in by_id["wp-standard-lab"].reasons
    assert by_id["wp-premium-lab"].eligible


def test_missing_or_expired_quote_makes_package_ineligible():
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=BUDGET,
        required_equipment=[],
        packages=_packages(),
        quotes_by_package={},  # no quotes at all
    )
    assert all(not c.eligible for c in checks)
    quotes = {
        "wp-starter-lab": {
            "quote_id": "Q-old",
            "status": "active",
            "expires_at": "2000-01-01T00:00:00+00:00",  # long expired
        }
    }
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=BUDGET,
        required_equipment=[],
        packages=_packages()[:1],
        quotes_by_package=quotes,
    )
    assert checks[0].reasons == ["quote_expired"]


def test_unavailable_package_ineligible():
    packages = [p.dict() for p in DEMO_PACKAGES[:1]]
    packages[0]["availability_status"] = "unavailable"
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=BUDGET,
        required_equipment=[],
        packages=packages,
        quotes_by_package={},
    )
    assert checks[0].reasons == ["unavailable", "quote_missing"]


def test_selection_rejects_blocked_packages():
    quotes = {
        p["package_id"]: {
            "quote_id": f"Q-{p['package_id']}",
            "status": "active",
            "expires_at": "2099-01-01T00:00:00+00:00",
        }
        for p in _packages()
    }
    checks = evaluate_packages(
        attendee_count=60,
        budget_inr_paise=BUDGET,
        required_equipment=[],
        packages=_packages(),
        quotes_by_package=quotes,
    )
    with pytest.raises(BudgetExceededError):
        validate_selection(checks, "wp-premium-lab")
    with pytest.raises(ValidationError):
        validate_selection(checks, "wp-nonexistent")  # forged id


def test_budget_ledger_rule():
    assert committed_plus_proposed_within_budget(1_450_000, 500_000, 2_000_000)
    assert not committed_plus_proposed_within_budget(1_450_000, 600_000, 2_000_000)


def test_deposit_constants_cover_catalog():
    for package in DEMO_PACKAGES:
        assert package.package_id in PACKAGE_DEPOSITS_ATFET
