"""Money rules: integer paise, Indian formatting, FET base units, parsing."""

from __future__ import annotations

import pytest

from shared.errors import ValidationError
from shared.inventory import PACKAGE_DEPOSITS_ATFET
from shared.validation import (
    format_fet,
    format_inr,
    parse_attendees,
    parse_budget_paise,
    parse_event_date,
    rupees_to_paise,
    validate_paise,
)


def test_paise_representation_of_demo_amounts():
    assert 20_000 * 100 == 2_000_000  # Rs 20,000 == 2,000,000 paise
    assert validate_paise(1_450_000) == 1_450_000


def test_floats_and_bools_are_rejected_for_money():
    with pytest.raises(ValidationError):
        validate_paise(14500.0)
    with pytest.raises(ValidationError):
        validate_paise(True)
    with pytest.raises(ValidationError):
        validate_paise("14500")


def test_negative_money_rejected():
    with pytest.raises(ValidationError):
        validate_paise(-1)


def test_indian_grouping_format():
    assert format_inr(1_450_000) == "₹14,500"
    assert format_inr(1_920_000) == "₹19,200"
    assert format_inr(2_600_000) == "₹26,000"
    assert format_inr(2_000_000) == "₹20,000"
    assert format_inr(20_000_000) == "₹2,00,000"
    assert format_inr(123_456_789) == "₹12,34,567.89"


def test_fet_formatting_without_floats():
    assert format_fet(10**17) == "0.1 tFET"
    assert format_fet(13 * 10**16) == "0.13 tFET"
    assert format_fet(3 * 10**16) == "0.03 tFET"


def test_deposit_deltas():
    starter = PACKAGE_DEPOSITS_ATFET["wp-starter-lab"]
    standard = PACKAGE_DEPOSITS_ATFET["wp-standard-lab"]
    premium = PACKAGE_DEPOSITS_ATFET["wp-premium-lab"]
    assert standard - starter == 3 * 10**16  # +0.03 tFET starter -> standard
    assert premium > standard > starter


def test_rupees_to_paise():
    assert rupees_to_paise(14500) == 1_450_000


def test_parse_budget_from_free_text():
    assert parse_budget_paise("Keep the supplier budget under ₹20,000.") == 2_000_000
    assert parse_budget_paise("budget of 20k") == 2_000_000
    assert parse_budget_paise("max 1.5 lakh") == 15_000_000
    assert parse_budget_paise("estimated expenses under Rs 25,000") == 2_500_000
    assert parse_budget_paise("no money mentioned here") is None


def test_parse_attendees_from_free_text():
    assert parse_attendees("organise a workshop for 60 people") == 60
    assert parse_attendees("60 attendees") == 60
    assert parse_attendees("for 75 participants") == 75
    assert parse_attendees("nothing numeric") is None


def test_parse_event_date_formats():
    assert parse_event_date("on November 15, 2026") == "2026-11-15"
    assert parse_event_date("15 November 2026") == "2026-11-15"
    assert parse_event_date("15 Nov 2026") == "2026-11-15"
    assert parse_event_date("2026-11-15") == "2026-11-15"
    assert parse_event_date("sometime next year") is None


def test_invalid_date_is_none_not_crash():
    assert parse_event_date("February 30, 2026") is None
