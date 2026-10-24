"""Explicit state machines for every persisted entity.

Every status referenced by the system lives here together with the set of
legal transitions. Services must use :func:`validate_transition` before
persisting a status change so that illegal jumps (for example
``proposed -> confirmed``) are impossible.
"""

from __future__ import annotations

from enum import Enum

from shared.errors import StateTransitionError


class StrEnum(str, Enum):
    """String enum whose JSON value is the plain string."""


class EventStatus(StrEnum):
    DRAFT = "draft"
    QUOTED = "quoted"
    APPROVED = "approved"
    EXECUTING = "executing"
    READY = "ready"
    BLOCKED = "blocked"


class QuoteStatus(StrEnum):
    ACTIVE = "active"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class ReservationStatus(StrEnum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    RESERVATION_PENDING = "reservation_pending"
    RESERVED = "reserved"
    PAYMENT_PENDING = "payment_pending"
    PAID = "paid"
    CONFIRMED = "confirmed"
    # terminal / failure outcomes
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"
    PAYMENT_FAILED = "payment_failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class PaymentStatus(StrEnum):
    NOT_STARTED = "not_started"
    CREATED = "created"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    VERIFIED_SIMULATED = "verified_simulated"
    REJECTED = "rejected"
    FAILED = "failed"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class PaymentMode(StrEnum):
    SIMULATED = "simulated"
    TESTNET = "testnet"


class InvitationStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    SENDING = "sending"
    SENT = "sent"
    LOGGED = "logged"  # console development mode: written to logs, never delivered
    FAILED = "failed"
    UNKNOWN = "unknown"
    STALE = "stale"


class RsvpStatus(StrEnum):
    CONFIRMED = "confirmed"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class PackageAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ActionType(StrEnum):
    RESERVE_PACKAGE = "reserve_package"
    SEND_INVITATION = "send_invitation"


class Actor(StrEnum):
    ORGANIZER = "organizer"
    COORDINATOR = "coordinator"
    SUPPLIER = "supplier"
    OUTREACH = "outreach"
    SYSTEM = "system"


class EntityType(StrEnum):
    EVENT = "event"
    PLAN = "plan"
    QUOTE = "quote"
    APPROVAL = "approval"
    RESERVATION = "reservation"
    PAYMENT = "payment"
    INVITATION = "invitation"
    RSVP = "rsvp"


class IneligibilityReason(StrEnum):
    CAPACITY_INSUFFICIENT = "capacity_insufficient"
    OVER_BUDGET = "over_budget"
    UNAVAILABLE = "unavailable"
    EQUIPMENT_MISSING = "equipment_missing"
    QUOTE_EXPIRED = "quote_expired"


class ResponseStatus(StrEnum):
    SUCCESS = "success"
    REJECTED = "rejected"
    PENDING = "pending"
    FAILED = "failed"


class ExternalEffect(StrEnum):
    """Honest record of whether an external side effect happened."""

    NONE = "none"
    INITIATED = "initiated"
    COMPLETED = "completed"
    UNCERTAIN = "uncertain"
    SIMULATED = "simulated"


# ---------------------------------------------------------------------------
# Transition tables (module-level so enum members are never shadowed)
# ---------------------------------------------------------------------------

RESERVATION_TRANSITIONS: dict[str, set[str]] = {
    ReservationStatus.PROPOSED.value: {
        ReservationStatus.AWAITING_APPROVAL.value,
        ReservationStatus.REJECTED.value,
    },
    ReservationStatus.AWAITING_APPROVAL.value: {
        ReservationStatus.APPROVED.value,
        ReservationStatus.REJECTED.value,
        ReservationStatus.EXPIRED.value,
        ReservationStatus.SUPERSEDED.value,
    },
    ReservationStatus.APPROVED.value: {
        ReservationStatus.RESERVATION_PENDING.value,
        ReservationStatus.CANCELLED.value,
    },
    ReservationStatus.RESERVATION_PENDING.value: {
        ReservationStatus.RESERVED.value,
        ReservationStatus.UNAVAILABLE.value,
        ReservationStatus.EXPIRED.value,
    },
    ReservationStatus.RESERVED.value: {
        ReservationStatus.PAYMENT_PENDING.value,
        ReservationStatus.SUPERSEDED.value,
        ReservationStatus.CANCELLED.value,
        ReservationStatus.EXPIRED.value,
    },
    ReservationStatus.PAYMENT_PENDING.value: {
        ReservationStatus.PAID.value,
        ReservationStatus.PAYMENT_FAILED.value,
        ReservationStatus.SUPERSEDED.value,
    },
    ReservationStatus.PAID.value: {ReservationStatus.CONFIRMED.value},
    ReservationStatus.CONFIRMED.value: {ReservationStatus.SUPERSEDED.value},
    ReservationStatus.REJECTED.value: set(),
    ReservationStatus.UNAVAILABLE.value: set(),
    ReservationStatus.EXPIRED.value: set(),
    ReservationStatus.PAYMENT_FAILED.value: set(),
    ReservationStatus.CANCELLED.value: set(),
    ReservationStatus.SUPERSEDED.value: set(),
}

PAYMENT_TRANSITIONS: dict[str, set[str]] = {
    PaymentStatus.NOT_STARTED.value: {PaymentStatus.CREATED.value},
    PaymentStatus.CREATED.value: {
        PaymentStatus.PENDING_VERIFICATION.value,
        PaymentStatus.FAILED.value,
    },
    PaymentStatus.PENDING_VERIFICATION.value: {
        PaymentStatus.VERIFIED.value,
        PaymentStatus.VERIFIED_SIMULATED.value,
        PaymentStatus.REJECTED.value,
        PaymentStatus.FAILED.value,
        PaymentStatus.EXPIRED.value,
        # unknown settlement must be reconciled with evidence, never retried blindly
        PaymentStatus.UNKNOWN.value,
    },
    PaymentStatus.VERIFIED.value: set(),
    PaymentStatus.VERIFIED_SIMULATED.value: set(),
    PaymentStatus.REJECTED.value: set(),
    PaymentStatus.FAILED.value: set(),
    PaymentStatus.EXPIRED.value: set(),
    PaymentStatus.UNKNOWN.value: {
        PaymentStatus.VERIFIED.value,
        PaymentStatus.VERIFIED_SIMULATED.value,
        PaymentStatus.FAILED.value,
    },
}

EVENT_TRANSITIONS: dict[str, set[str]] = {
    EventStatus.DRAFT.value: {EventStatus.QUOTED.value, EventStatus.BLOCKED.value},
    EventStatus.QUOTED.value: {
        EventStatus.APPROVED.value,
        EventStatus.QUOTED.value,
        EventStatus.BLOCKED.value,
    },
    EventStatus.APPROVED.value: {EventStatus.EXECUTING.value, EventStatus.QUOTED.value},
    EventStatus.EXECUTING.value: {
        EventStatus.READY.value,
        EventStatus.EXECUTING.value,
        EventStatus.QUOTED.value,
        EventStatus.BLOCKED.value,
    },
    EventStatus.READY.value: {EventStatus.QUOTED.value},  # replan reopens quoting
    EventStatus.BLOCKED.value: {EventStatus.QUOTED.value},
}


def validate_transition(table: dict[str, set[str]], current: str, target: str) -> None:
    """Raise :class:`StateTransitionError` when the move is not in ``table``."""
    allowed = table.get(current, set())
    if target not in allowed:
        raise StateTransitionError(
            f"Illegal transition {current} -> {target}; "
            f"allowed: {sorted(allowed) or 'none (terminal)'}"
        )
