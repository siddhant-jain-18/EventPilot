"""State machine transition legality (spec section 5)."""

from __future__ import annotations

import pytest

from shared.enums import (
    EVENT_TRANSITIONS,
    PAYMENT_TRANSITIONS,
    RESERVATION_TRANSITIONS,
    validate_transition,
)
from shared.errors import StateTransitionError


@pytest.mark.parametrize(
    "current,target",
    [
        ("proposed", "confirmed"),  # the forbidden jump
        ("reserved", "paid"),  # must pass through payment_pending
        ("confirmed", "reserved"),
        ("paid", "payment_pending"),
        ("superseeded" if False else "superseded", "reserved"),
    ],
)
def test_illegal_reservation_transitions(current, target):
    with pytest.raises(StateTransitionError):
        validate_transition(RESERVATION_TRANSITIONS, current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        ("proposed", "awaiting_approval"),
        ("awaiting_approval", "approved"),
        ("approved", "reservation_pending"),
        ("reservation_pending", "reserved"),
        ("reserved", "payment_pending"),
        ("payment_pending", "paid"),
        ("paid", "confirmed"),
        ("reserved", "superseded"),
        ("payment_pending", "payment_failed"),
    ],
)
def test_legal_reservation_transitions(current, target):
    validate_transition(RESERVATION_TRANSITIONS, current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        ("not_started", "verified"),  # cannot skip verification
        ("created", "verified"),
        ("pending_verification", "created"),
        ("verified", "pending_verification"),  # terminal
        ("verified_simulated", "verified"),  # modes never mix
    ],
)
def test_illegal_payment_transitions(current, target):
    with pytest.raises(StateTransitionError):
        validate_transition(PAYMENT_TRANSITIONS, current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        ("created", "pending_verification"),
        ("pending_verification", "verified"),
        ("pending_verification", "verified_simulated"),
        ("pending_verification", "unknown"),
        ("unknown", "verified"),  # reconciliation path
        ("pending_verification", "rejected"),
        ("pending_verification", "failed"),
    ],
)
def test_legal_payment_transitions(current, target):
    validate_transition(PAYMENT_TRANSITIONS, current, target)


def test_event_replan_reopens_quoting():
    validate_transition(EVENT_TRANSITIONS, "ready", "quoted")
    validate_transition(EVENT_TRANSITIONS, "executing", "quoted")
    with pytest.raises(StateTransitionError):
        validate_transition(EVENT_TRANSITIONS, "draft", "ready")
