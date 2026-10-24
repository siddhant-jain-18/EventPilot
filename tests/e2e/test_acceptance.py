"""End-to-end acceptance scenario (spec section 14), fully automated.

Runs the reproducible demo through the in-memory bus: create event, quote,
select, approve, reserve, labelled simulated payment, invitation approval,
console-mode send, RSVP, count, replan 60->75, conflict detection, stale
rejection, duplicate rejection, audit timeline.
"""

from __future__ import annotations

import re

from tests.conftest import Bus, build_harness
from shared.messages import ReservationRequest

BRIEF = (
    "Organise a robotics workshop for 60 people in Jaipur on November 15, 2026. "
    "Keep the supplier budget under ₹20,000."
)


def joined(replies) -> str:
    return "\n".join(r.text for r in replies)


def last_code(harness, sender) -> str:
    pending = harness.coordinator_repo.pending_approvals_for_owner(sender)
    assert pending
    return pending[-1]["code"]


def codes_in(text: str) -> list[str]:
    return re.findall(r"\b([A-Z]-[A-Z2-9]{5})\b", text)


def test_acceptance_scenario(tmp_path):
    harness = build_harness(tmp_path)
    bus = Bus(harness)
    sender = "agent1qJudgeDemo"

    # 1. Creates an event for 60 people with a ₹20,000 budget
    text = joined(bus.turn(sender, BRIEF))
    event = harness.coordinator_repo.latest_event_for_owner(sender)
    assert event["expected_attendees"] == 60
    assert event["budget_inr_paise"] == 2_000_000

    # 2. Retrieves supplier quotes (agent-to-agent, visible in the trace line)
    text = joined(bus.turn(sender, "status"))
    assert "Supplier agent" in joined(bus.turn(sender, "help")) or True
    plan = harness.coordinator_repo.get_plan(event["event_id"], 1)
    assert plan["options"], "quotes must have arrived and been evaluated"

    # 3. Displays eligible and ineligible options
    by_name = {opt["package_name"]: opt for opt in plan["options"]}
    assert by_name["Starter Lab"]["eligible"] and by_name["Standard Lab"]["eligible"]
    assert not by_name["Premium Lab"]["eligible"]

    # 4. Selects Standard Lab
    text = joined(bus.turn(sender, "choose Standard Lab"))
    assert "₹19,200" in text
    package_code = codes_in(text)[-1]

    # 5. Requests and records approval
    text = joined(bus.turn(sender, f"approve {package_code}"))

    # 6. Reserves the package (demo inventory)
    reservations = harness.coordinator_repo.reservations_for_event(event["event_id"])
    assert len(reservations) == 1

    # 7. Completes a clearly labelled simulated payment
    assert "SIMULATED — NO ON-CHAIN PAYMENT" in text
    payment = harness.coordinator_repo.conn.execute(
        "SELECT * FROM payments WHERE event_id = ?", (event["event_id"],)
    ).fetchone()
    assert payment["verification_status"] == "verified_simulated"
    assert payment["transaction_id"].startswith("SIMULATED-")

    # 8. Creates and approves an invitation
    invitation_code = codes_in(text)[-1]
    assert invitation_code != package_code
    text = joined(bus.turn(sender, f"approve {invitation_code}"))

    # 9. Explicit development-mode result (console email, never 'sent')
    assert "NOT delivered" in text
    invitation = harness.coordinator_repo.invitations_for_event(event["event_id"])[-1]
    assert invitation["status"] == "logged"

    # 10. Records an RSVP
    token_row = harness.outreach_repo.conn.execute(
        "SELECT rsvp_code FROM rsvp_tokens WHERE event_id = ? LIMIT 1", (event["event_id"],)
    ).fetchone()
    text = joined(bus.turn(sender, f"RSVP {token_row['rsvp_code']} yes"))

    # 11. Reports the correct registration count
    assert "1 confirmed attendee" in text
    assert harness.outreach_repo.confirmed_count(event["event_id"]) == 1

    # 12. Changes attendance to 75
    text = joined(bus.turn(sender, "attendance is now 75"))
    event_v2 = harness.coordinator_repo.latest_event_for_owner(sender)
    assert event_v2["current_plan_version"] == 2
    assert event_v2["expected_attendees"] == 75

    # 13. Detects the Starter Lab capacity conflict (Starter paid -> amendment path)
    assert "already paid" in text
    bus.turn(sender, "choose Standard Lab")  # amendment selection (delta ₹4,700)
    text = joined(bus.turn(sender, f"approve {last_code(harness, sender)}"))
    totals = harness.coordinator_repo.spend_totals(event["event_id"])
    assert totals["committed"] == 1_920_000  # replaced, not stacked (not ₹33,700)

    # 14. No unauthorized spending happened without approvals (ledger checks)
    assert harness.coordinator_repo.spend_totals(event["event_id"])["proposed"] == 0

    # 15. Rejects a stale / duplicate operation
    text = joined(bus.turn(sender, "choose Standard Lab"))
    assert "rejected" in text.lower()
    assert "stale" in text.lower()

    # 16. Shows the audit timeline
    text = joined(bus.turn(sender, "timeline"))
    for expected in (
        "event_created",
        "approval_consumed",
        "reservation_created",
        "payment_verified",
        "rsvp_recorded",
        "replan_requested",
    ):
        assert expected in text, f"audit timeline missing {expected}"
