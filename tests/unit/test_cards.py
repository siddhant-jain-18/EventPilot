"""Interactive Card builders/validators on the official SDK helpers."""

from __future__ import annotations

import json

import pytest

from shared.cards import (
    MAX_PAYLOAD_BYTES,
    build_form_card,
    build_package_carousel,
    build_review_card,
    parse_card_response,
)
from shared.errors import ValidationError


def _options():
    return [
        {
            "package_id": "wp-starter-lab",
            "quote_id": "Q-1",
            "name": "Starter Lab",
            "capacity": 60,
            "price_inr_paise": 1_450_000,
            "deposit_fet_base": 10**17,
            "eligible": True,
            "reasons": [],
        },
        {
            "package_id": "wp-premium-lab",
            "quote_id": "Q-3",
            "name": "Premium Lab",
            "capacity": 100,
            "price_inr_paise": 2_600_000,
            "deposit_fet_base": 18 * 10**16,
            "eligible": False,
            "reasons": ["over_budget"],
        },
    ]


def test_carousel_metadata_is_official_and_string_valued():
    card = build_package_carousel(title="Options", options=_options())
    metadata = card["block"].metadata
    assert metadata["card_protocol_version"] == "1"
    assert metadata["requires_card_interaction"] == "true"
    assert metadata["card_kind"] == "carousel"
    payload = json.loads(metadata["card_payload"])
    assert len(payload["items"]) == 2
    for key, value in metadata.items():
        assert isinstance(key, str) and isinstance(value, str)


def test_form_and_review_cards_validate():
    form = build_form_card(
        title="Brief",
        submit_label="Submit",
        submit_selection={"type": "submit_event_form"},
    )
    assert form["card_kind"] == "form"
    review = build_review_card(
        title="Review",
        rows=[("Package", "Standard Lab"), ("Amount", "₹19,200")],
        approve_label="Approve A-7F3K",
        approve_selection={"type": "approve_package", "approval_code": "A-7F3K"},
    )
    assert review["card_kind"] == "review"
    assert json.loads(review["block"].metadata["card_payload"])["summary_rows"][1]["value"] == "₹19,200"


def test_oversized_payload_rejected():
    big = "x" * (MAX_PAYLOAD_BYTES + 1)
    metadata = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": "carousel",
        "card_payload": json.dumps({"pad": big}),
    }
    from shared.cards import _validated_metadata_payload

    with pytest.raises(ValidationError):
        _validated_metadata_payload(metadata)


def test_non_string_metadata_rejected():
    from shared.cards import _validated_metadata_payload

    with pytest.raises(ValidationError):
        _validated_metadata_payload({"card_protocol_version": 1})


def test_selection_response_roundtrip():
    from uagents_core.contrib.protocols.chat.cards import create_card_response_content

    block = create_card_response_content(
        card_id=None, selection={"type": "select_package", "package_id": "wp-standard-lab"}
    )
    response = parse_card_response(block.metadata)
    assert response is not None
    assert response.selection["package_id"] == "wp-standard-lab"


def test_malformed_selection_json_raises():
    metadata = {"card_protocol_version": "1", "selection": "{not json"}
    with pytest.raises(ValidationError):
        parse_card_response(metadata)


def test_forged_package_id_is_not_trusted_at_build_time():
    """The card only echoes ids; the coordinator re-validates them server-side
    (covered end-to-end in the integration tests). Here we assert the carousel
    never embeds a price the server could mistake for authoritative."""
    card = build_package_carousel(title="Options", options=_options())
    payload = json.loads(card["block"].metadata["card_payload"])
    text = json.dumps(payload)
    assert "1450000" not in text  # paise amounts never ride in the card
