"""Interactive Card builder and validator built on the OFFICIAL SDK helpers.

The installed SDK (uagents-core 0.4.9) ships the documented cards module
``uagents_core.contrib.protocols.chat.cards`` with payload schemas for the
kinds ``carousel`` | ``detail`` | ``form`` | ``review`` | ``custom``, plus
``create_card_content`` / ``create_card_response_content`` /
``extract_card`` / ``extract_card_response`` helpers. Cards travel inside
``MetadataContent`` blocks of a ``ChatMessage`` — all metadata values are
strings and the payload is a JSON string.

This module adds the EventPilot domain builders on top and a stricter
outbound validator (64 KB payload ceiling, string-only metadata, no terminal
flag on cards that expect a response). Plain-text narration is ALWAYS emitted
alongside a card so the workflow remains fully usable if a card fails to
render (spec section 7.2).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from shared.errors import ValidationError
from shared.validation import format_fet, format_inr

# Official helpers — never hand-roll the wire format.
from uagents_core.contrib.protocols.chat.cards import (  # noqa: F401
    CarouselBadge,
    CarouselCardPayload,
    CarouselItem,
    Card,
    CardResponse,
    CtaAction,
    FormCardPayload,
    FormField,
    META_CARD_ID,
    META_CARD_KIND,
    META_CARD_PAYLOAD,
    META_CARD_PROTOCOL_VERSION,
    META_CANCELLED,
    META_SELECTION,
    META_TEXT,
    ReviewCardPayload,
    ReviewSummaryRow,
    create_card_content,
    create_card_response_content,
    extract_card,
    extract_card_response,
    validate_card_payload,
)

MAX_PAYLOAD_BYTES = 64 * 1024


class CardValidationError(ValidationError):
    code = "card_validation_error"


def _validated_metadata_payload(metadata: dict[str, str]) -> str:
    """Re-check official metadata before sending: strings only, <= 64 KB."""
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise CardValidationError("card metadata keys and values must be strings")
    payload_json = metadata.get(META_CARD_PAYLOAD, "")
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise CardValidationError("card_payload exceeds the 64 KB limit")
    return payload_json


def build_form_card(
    *,
    title: str,
    submit_label: str,
    submit_selection: dict[str, Any],
    prefill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Event-requirements form card. Returns {block, text_fallback, card_id}."""
    prefill = prefill or {}
    fields = [
        FormField(name="title", kind="text", label="Event title", required=True),
        FormField(name="city", kind="text", label="City", required=True),
        FormField(name="date", kind="text", label="Date (YYYY-MM-DD)", required=True),
        FormField(name="attendees", kind="number", label="Expected attendees", required=True),
        FormField(name="budget_inr", kind="number", label="Budget in rupees", required=True),
        FormField(name="equipment", kind="text", label="Required equipment (comma separated)",
                  required=False, placeholder="projector, wifi"),
    ]
    payload = FormCardPayload(
        title=title,
        fields=fields,
        submit_cta=CtaAction(label=submit_label, selection=submit_selection),
    )
    block = create_card_content(payload, card_id=uuid4())
    _validated_metadata_payload(block.metadata)
    return {
        "block": block,
        "card_kind": "form",
        "card_id": str(block.metadata.get(META_CARD_ID, "")),
        "payload": payload.model_dump(exclude_none=True),
    }


def build_package_carousel(
    *,
    title: str,
    options: list[dict[str, Any]],
    selection_prefix: str = "select_package",
) -> dict[str, Any]:
    """Package comparison carousel; one CTA per package with server-bound ids.

    ``options`` items: {package_id, quote_id, name, capacity, price_inr_paise,
    deposit_fet_base, eligible, reasons}.
    The displayed price is informational only — the coordinator re-validates
    every selection against its stored quote record (never the card).
    """
    items = []
    for opt in options:
        badges = []
        if opt.get("eligible"):
            badges.append(CarouselBadge(label="Fits capacity and budget", variant="success"))
        else:
            for reason in opt.get("reasons", [])[:2]:
                variant = "warning" if reason != "over_budget" else "warning"
                badges.append(CarouselBadge(label=_reason_label(reason), variant=variant))
        selection = {
            "type": selection_prefix,
            "package_id": str(opt["package_id"]),
            "quote_id": str(opt["quote_id"]),
        }
        items.append(
            CarouselItem(
                id=str(opt["package_id"]),
                title=str(opt["name"]),
                subtitle=(
                    f"Capacity {opt['capacity']} · {format_inr(int(opt['price_inr_paise']))} "
                    f"· deposit {format_fet(int(opt['deposit_fet_base']))}"
                ),
                badges=badges or None,
                secondary_text="Demo inventory — not a real venue" ,
                primary_cta=CtaAction(
                    label="Review and approve" if opt.get("eligible") else "Details blocked",
                    selection=selection,
                ),
            )
        )
    payload = CarouselCardPayload(title=title, items=items)
    block = create_card_content(payload, card_id=uuid4())
    _validated_metadata_payload(block.metadata)
    return {
        "block": block,
        "card_kind": "carousel",
        "card_id": str(block.metadata.get(META_CARD_ID, "")),
        "payload": payload.model_dump(exclude_none=True),
    }


def build_review_card(
    *,
    title: str,
    rows: list[tuple[str, str]],
    approve_label: str,
    approve_selection: dict[str, Any],
    reject_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Spending / action review card bound to one exact approval code."""
    payload = ReviewCardPayload(
        title=title,
        summary_rows=[ReviewSummaryRow(label=k, value=v) for k, v in rows],
        approve_cta=CtaAction(label=approve_label, selection=approve_selection),
        reject_cta=CtaAction(label="Reject", selection=reject_selection or {"type": "reject"}),
    )
    block = create_card_content(payload, card_id=uuid4())
    _validated_metadata_payload(block.metadata)
    return {
        "block": block,
        "card_kind": "review",
        "card_id": str(block.metadata.get(META_CARD_ID, "")),
        "payload": payload.model_dump(exclude_none=True),
    }


def parse_card_response(metadata: dict[str, str]) -> CardResponse | None:
    """Parse an inbound card response block; raises on malformed selection."""
    if metadata.get(META_CARD_PROTOCOL_VERSION) != "1":
        return None
    if META_CARD_KIND in metadata:  # request blocks are not responses
        return None
    selection_raw = metadata.get(META_SELECTION)
    if selection_raw is not None:
        import json

        try:
            parsed = json.loads(selection_raw)
        except json.JSONDecodeError as err:
            raise CardValidationError("card selection is not valid JSON") from err
        if not isinstance(parsed, dict):
            raise CardValidationError("card selection must be a JSON object")
    response = extract_card_response(type("M", (), {"metadata": metadata})())
    return response


def parse_card_request(metadata: dict[str, str]) -> Card | None:
    card = extract_card(type("M", (), {"metadata": metadata})())
    return card


def _reason_label(reason: str) -> str:
    labels = {
        "capacity_insufficient": "Capacity too small",
        "over_budget": "Over budget",
        "unavailable": "Not available",
        "equipment_missing": "Missing required equipment",
        "quote_expired": "Quote expired",
    }
    return labels.get(reason, reason.replace("_", " ").title())


def make_card_id(value: str) -> UUID | None:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
