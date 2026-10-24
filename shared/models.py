"""Shared domain models (pydantic v1 API, wire-compatible with uAgents).

All models use the pydantic v1 BaseModel because ``uagents_core.models.Model``
is built on it; when uAgents is installed the exact same base class is used so
message models register cleanly with uAgents protocols. Money fields are
strict integers (paise / atto-FET) — floats never pass validation.
"""

from __future__ import annotations

from typing import Any, Optional

try:  # pragma: no cover - depends on environment
    from uagents_core.models import Model as _WireBase  # type: ignore
except Exception:  # pragma: no cover - fallback for service-only environments
    from pydantic.v1 import BaseModel as _WireBase  # type: ignore

from pydantic.v1 import Field

from shared.enums import (
    ActionType,
    ApprovalStatus,
    EventStatus,
    ExternalEffect,
    IneligibilityReason,
    InvitationStatus,
    PackageAvailability,
    PaymentMode,
    PaymentStatus,
    QuoteStatus,
    ReservationStatus,
    RsvpStatus,
)


class StrictModel(_WireBase):
    class Config:
        extra = "forbid"
        validate_assignment = True
        use_enum_values = True


def money_field(**kwargs: Any) -> Any:
    kwargs.setdefault("ge", 0)
    kwargs.setdefault("strict", True)
    return Field(**kwargs)


def count_field(**kwargs: Any) -> Any:
    kwargs.setdefault("strict", True)
    return Field(**kwargs)


# ---------------------------------------------------------------------------
# Inventory / planning
# ---------------------------------------------------------------------------


class SupplierPackage(StrictModel):
    package_id: str
    name: str
    capacity: int = count_field(ge=1, le=100000)
    price_inr_paise: int = money_field()
    equipment: list[str] = Field(default_factory=list)
    inclusions: list[str] = Field(default_factory=list)
    availability_status: str = PackageAvailability.AVAILABLE.value


class EligibilityCheck(StrictModel):
    package_id: str
    package_name: str
    capacity: int
    price_inr_paise: int
    deposit_fet_base: int = money_field()
    quote_id: Optional[str] = None
    eligible: bool = True
    reasons: list[str] = Field(default_factory=list)
    headroom_inr_paise: Optional[int] = money_field()


# ---------------------------------------------------------------------------
# Event and plan
# ---------------------------------------------------------------------------


class Event(StrictModel):
    event_id: str
    owner_sender: str
    title: str
    event_type: str = "workshop"
    date: str  # ISO date
    timezone: str = "Asia/Kolkata"
    city: str
    expected_attendees: int = count_field(ge=1, le=10000)
    budget_inr_paise: int = money_field()
    equipment_requirements: list[str] = Field(default_factory=list)
    status: str = EventStatus.DRAFT.value
    current_plan_version: int = count_field(default=1, ge=1)
    created_at: str
    updated_at: str


class PlanRecord(StrictModel):
    """Immutable snapshot of one plan version and the options it presented."""

    event_id: str
    plan_version: int = count_field(ge=1)
    attendee_count: int = count_field(ge=1)
    options: list[EligibilityCheck] = Field(default_factory=list)
    created_at: str


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------


class Quote(StrictModel):
    quote_id: str
    event_id: str
    plan_version: int
    supplier_id: str
    package_id: str
    package_name: str
    capacity: int
    attendee_count: int
    price_inr_paise: int = money_field()
    currency: str = "INR"
    deposit_fet_base: int = money_field()
    inclusions: list[str] = Field(default_factory=list)
    status: str = QuoteStatus.ACTIVE.value
    created_at: str
    expires_at: str
    amendment_of_reservation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


class Approval(StrictModel):
    approval_id: str
    code: str
    event_id: str
    owner_sender: str
    plan_version: int
    action_type: str  # ActionType value
    payload: dict[str, Any]
    action_payload_hash: str
    amount_inr_paise: Optional[int] = money_field()
    recipient_set_hash: Optional[str] = None
    status: str = ApprovalStatus.PENDING.value
    created_at: str
    expires_at: str
    consumed_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Reservation / payment
# ---------------------------------------------------------------------------


class Reservation(StrictModel):
    reservation_id: str
    event_id: str
    plan_version: int
    quote_id: str
    package_id: str
    status: str = ReservationStatus.PROPOSED.value
    payment_mode: Optional[str] = PaymentMode.SIMULATED.value
    action_id: str
    created_at: str
    updated_at: str


class Payment(StrictModel):
    payment_id: str
    reservation_id: str
    quote_id: str
    event_id: str
    expected_amount_fet_base: int = money_field()
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""
    transaction_id: Optional[str] = None
    verification_status: str = PaymentStatus.NOT_STARTED.value
    payment_mode: str = PaymentMode.SIMULATED.value
    amount_inr_paise: Optional[int] = money_field()
    created_at: str
    verified_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Invitation / RSVP
# ---------------------------------------------------------------------------


class Invitation(StrictModel):
    invitation_id: str
    event_id: str
    plan_version: int
    draft_version: int = count_field(ge=1)
    subject: str
    body: str
    content_hash: str
    recipient_count: int = count_field(ge=0)
    recipient_set_hash: str
    approval_id: Optional[str] = None
    status: str = InvitationStatus.DRAFT.value
    provider_message_ids: list[str] = Field(default_factory=list)
    idempotency_key: Optional[str] = None
    mode: str = "console"
    created_at: str
    sent_at: Optional[str] = None
    failure_reason: Optional[str] = None


class Rsvp(StrictModel):
    rsvp_id: str
    event_id: str
    token_id: str
    attendee_reference: str
    status: str = RsvpStatus.CONFIRMED.value
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEvent(StrictModel):
    audit_id: str
    event_id: Optional[str] = None
    correlation_id: str = ""
    actor: str  # Actor value
    action: str
    entity_type: str
    entity_id: str
    result: str
    timestamp: str
    safe_metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Brief (validated user intent)
# ---------------------------------------------------------------------------


class BriefDraft(StrictModel):
    """Structured, validated outcome of intent extraction.

    Deterministic parsing produces it first; the ASI1 LLM may only fill fields
    that deterministic parsing could not, and every value is re-validated here.
    """

    title: Optional[str] = None
    event_type: Optional[str] = None
    date: Optional[str] = None  # ISO date
    timezone: str = "Asia/Kolkata"
    city: Optional[str] = None
    expected_attendees: Optional[int] = Field(default=None, ge=1, le=10000, strict=True)
    budget_inr_paise: Optional[int] = Field(default=None, ge=0, strict=True)
    equipment_requirements: list[str] = Field(default_factory=list)
    raw_text: str = ""

    def missing_mandatory(self) -> list[str]:
        missing = []
        if not self.expected_attendees:
            missing.append("expected_attendees")
        if not self.budget_inr_paise:
            missing.append("budget_inr_paise")
        if not self.date:
            missing.append("date")
        if not self.city:
            missing.append("city")
        return missing


class OptionView(StrictModel):
    """Presentation-ready view of one option (already computed server-side)."""

    position: int
    package_id: str
    package_name: str
    quote_id: str
    capacity: int
    price_inr_paise: int
    deposit_fet_base: int
    eligible: bool
    reasons: list[str] = Field(default_factory=list)
    headroom_inr_paise: Optional[int] = None
    expires_at: str
