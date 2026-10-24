"""Numeric guard, allowlist and hashing helpers."""

from __future__ import annotations

import pytest

from shared.errors import ValidationError
from shared.validation import (
    build_allowed_numbers,
    canonical_hash,
    content_hash,
    extract_json_object,
    normalize_equipment,
    numeric_guard,
    validate_allowlisted_email,
)


FACTS = build_allowed_numbers(
    2_000_000,  # budget paise
    1_450_000,
    1_920_000,
    2_600_000,
    60,
    80,
    100,
    80_000,
)


def test_guard_accepts_known_facts_in_common_formats():
    text = (
        "Starter Lab costs ₹14,500 and Standard Lab ₹19,200; both fit 60 attendees "
        "with headroom ₹80,000 under the ₹20,000 budget. Premium (₹26,000) is blocked."
    )
    ok, offending = numeric_guard(text, FACTS)
    assert ok, offending


def test_guard_rejects_unknown_numbers():
    text = "Standard Lab costs ₹19,200 (only ₹15,000 with my discount!)"
    ok, offending = numeric_guard(text, FACTS)
    assert not ok
    assert any("15000" in token for token in offending)


def test_guard_rejects_unrecognised_plain_numbers():
    ok, _ = numeric_guard("The venue holds 5000 people.", FACTS)
    assert not ok


def test_canonical_hash_is_order_insensitive():
    assert canonical_hash({"a": 1, "b": [2, 3]}) == canonical_hash({"b": [2, 3], "a": 1})
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})


def test_content_hash_differs_for_modified_invitation():
    h1 = content_hash("Subject: hi\nBody: hello")
    h2 = content_hash("Subject: hi\nBody: hello!")
    assert h1 != h2


def test_allowlist_exact_match_only():
    allow = ["alice@example.test", "bob@example.test"]
    assert validate_allowlisted_email("alice@example.test", allow)
    assert validate_allowlisted_email("ALICE@EXAMPLE.TEST", allow)
    assert not validate_allowlisted_email("mallory@evil.test", allow)
    # domain-wide matches must NOT pass
    assert not validate_allowlisted_email("someone@something.example.test", allow)
    assert not validate_allowlisted_email("", allow)


def test_normalize_equipment_tokens():
    assert normalize_equipment(["Projector", "Wi-Fi", "wi fi"]) == ["projector", "wi_fi"]
    assert normalize_equipment("Recording Setup") == ["recording_setup"]


def test_extract_json_object_handles_fences():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}
    assert extract_json_object("no json here") is None
