"""Typed, versioned inter-agent message contracts (spec section 6).

Every request carries: ``schema_version``, ``request_id`` (unique),
``correlation_id``, ``event_id`` and ``plan_version`` where applicable.
Every response echoes ``request_id``, carries a typed ``status``, a structured
``error`` when it failed, and an honest ``side_effect`` indication describing
whether an external side effect (payment, e-mail, reservation) occurred.

Wire models extend the same base class uAgents uses (pydantic v1), so they can
be registered directly on uAgents Protocols.
"""

from __future__ import annotations

from typing import Any, ClassVar, Optional
from uuid import uuid4

from pydantic.v1 import Field

from shared.enums import ExternalEffect, ResponseStatus
from shared.models import StrictModel

SCHEMA_VERSION = "1.0.0"


def _rid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12].upper()}"


class ErrorResponse(StrictModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class _RequestBase(StrictModel):
    TYPE: ClassVar[str] = "request"

    schema_version: str = SCHEMA_VERSION
    request_id: str = Field(default_factory=lambda: _rid("REQ"))
    correlation_id: str = ""
    event_id: str = ""
    plan_version: Optional[int] = None
    requested_at: str = ""
    requested_by: str = "coordinator"


class _ResponseBase(StrictModel):
    TYPE: ClassVar[str] = "response"

    schema_version: str = SCHEMA_VERSION
    request_id: str  # echoed from the request
    correlation_id: str = ""
    event_id: str = ""
    plan_version: Optional[int] = None
    status: str = ResponseStatus.SUCCESS.value
    error: Optional[ErrorResponse] = None
    side_effect: str = ExternalEffect.NONE.value

    @classmethod
    def failure(cls, request_id: str, err: Exception, **kwargs: Any) -> "_ResponseBase":
        from shared.errors import EventPilotError

        payload = {
            "request_id": request_id,
            "status": ResponseStatus.REJECTED.value
            if isinstance(err, EventPilotError)
            else ResponseStatus.FAILED.value,
            "error": ErrorResponse(
                code=getattr(err, "code", "internal_error"),
                message=str(err),
                retryable=False,
                details=getattr(err, "details", {}) or {},
            ),
            **kwargs,
        }
        return cls(**payload)


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


class QuoteRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.quote_request"
    attendee_count: int = Field(ge=1, le=10000)
    equipment_requirements: list[str] = Field(default_factory=list)
    amendment_of_reservation_id: Optional[str] = None


class QuoteLine(StrictModel):
    quote_id: str
    package_id: str
    package_name: str
    capacity: int
    attendee_count: int
    price_inr_paise: int = Field(ge=0, strict=True)
    currency: str = "INR"
    deposit_fet_base: int = Field(ge=0, strict=True)
    inclusions: list[str] = Field(default_factory=list)
    created_at: str
    expires_at: str
    amendment_of_reservation_id: Optional[str] = None


class QuoteResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.quote_response"
    supplier_id: str = ""
    quotes: list[QuoteLine] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------


class ReservationRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.reservation_request"
    quote_id: str
    package_id: str
    attendee_count: int
    action_id: str  # idempotency key
    approval_id: str
    amendment_of_reservation_id: Optional[str] = None


class PaymentRequirement(StrictModel):
    quote_id: str
    reservation_id: str
    expected_amount_fet_base: int = Field(ge=0, strict=True)
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""
    expires_at: str = ""


class ReservationResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.reservation_response"
    reservation_id: str = ""
    reservation_status: str = ""
    payment_requirement: Optional[PaymentRequirement] = None
    replayed: bool = False


class ReservationSupersedeRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.reservation_supersede_request"
    reservation_id: str
    action_id: str
    reason: str = ""


class ReservationSupersedeResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.reservation_supersede_response"
    reservation_id: str = ""
    reservation_status: str = ""


# ---------------------------------------------------------------------------
# Payment status
# ---------------------------------------------------------------------------


class PaymentStatusRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.payment_status_request"
    reservation_id: str
    quote_id: str
    payment_id: str
    transaction_id: str  # evidence id; never sufficient on its own
    amount_fet_base: int = Field(ge=0, strict=True)
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""
    action_id: str


class PaymentStatusResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.payment_status_response"
    payment_id: str = ""
    reservation_id: str = ""
    verification_status: str = ""
    simulated: bool = False
    explorer_url: Optional[str] = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class InvitationDraftRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.invitation_draft_request"
    title: str
    event_type: str = "workshop"
    date: str
    timezone: str = "Asia/Kolkata"
    city: str
    expected_attendees: int
    note: str = ""  # e.g. "attendance changed 60 -> 75; invite update required"


class InvitationDraftResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.invitation_draft_response"
    invitation_id: str = ""
    draft_version: int = 1
    subject: str = ""
    body: str = ""
    content_hash: str = ""
    recipient_count: int = 0
    recipient_set_hash: str = ""
    superseded_invitation_id: Optional[str] = None


class SendInvitationRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.send_invitation_request"
    invitation_id: str
    draft_version: int
    content_hash: str
    recipient_set_hash: str
    approval_id: str
    action_id: str


class SendResultLine(StrictModel):
    recipient_ref: str
    status: str  # sent | logged_console | failed | unknown
    provider_message_id: str = ""
    # Populated only for console mode (nothing was emailed): lets the local
    # demo exercise the deduplicated RSVP flow without reading the database.
    rsvp_code: str = ""


class SendInvitationResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.send_invitation_response"
    invitation_id: str = ""
    invitation_status: str = ""
    provider_message_ids: list[str] = Field(default_factory=list)
    results: list[SendResultLine] = Field(default_factory=list)
    uncertain: bool = False


# ---------------------------------------------------------------------------
# RSVP
# ---------------------------------------------------------------------------


class RsvpCountRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.rsvp_count_request"


class RsvpCountResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.rsvp_count_response"
    confirmed_count: int = 0
    capacity: Optional[int] = None


class RsvpSubmitRequest(_RequestBase):
    TYPE: ClassVar[str] = "workshoppilot.rsvp_submit_request"
    rsvp_code: str
    decision: str = "yes"  # yes | no
    attendee_reference: str = ""


class RsvpSubmitResponse(_ResponseBase):
    TYPE: ClassVar[str] = "workshoppilot.rsvp_submit_response"
    confirmed_count: int = 0
    already_registered: bool = False


ALL_MESSAGE_CLASSES: list[type] = [
    QuoteRequest, QuoteResponse,
    ReservationRequest, ReservationResponse,
    ReservationSupersedeRequest, ReservationSupersedeResponse,
    PaymentStatusRequest, PaymentStatusResponse,
    InvitationDraftRequest, InvitationDraftResponse,
    SendInvitationRequest, SendInvitationResponse,
    RsvpCountRequest, RsvpCountResponse,
    RsvpSubmitRequest, RsvpSubmitResponse,
]
