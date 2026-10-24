"""Multi-agent workflow integration tests through the in-memory bus."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from agents.outreach.service import OutreachService
from shared.messages import ReservationRequest, SendInvitationRequest
from tests.conftest import Bus, build_harness

BRIEF = (
    "Plan a robotics workshop for 60 people in Jaipur on 15 November 2026 "
    "with a budget under ₹20,000."
)


def joined(replies) -> str:
    return "\n".join(r.text for r in replies)


def extract_last_code(text: str) -> str:
    matches = re.findall(r"\b([A-Z]-[A-Z2-9]{5})\b", text)
    assert matches, f"no approval code found in: {text[:400]}"
    return matches[-1]


def last_pending_code(harness, sender: str) -> str:
    pending = harness.coordinator_repo.pending_approvals_for_owner(sender)
    assert pending, "expected a pending approval"
    return pending[-1]["code"]


def test_cold_start_greeting(bus):
    text = joined(bus.turn("agent1qJudge1", "hello"))
    assert "WorkshopPilot" in text
    assert "DEMO DATA" in text


def test_capability_question_with_punctuation_is_a_greeting(bus):
    for prompt in ("what can you do?", "What do you do?", "who are you?"):
        text = joined(bus.turn("agent1qGreeting", prompt))
        assert "WorkshopPilot here" in text
        assert "DEMO DATA" in text


def test_off_topic_is_graceful(bus):
    text = joined(bus.turn("agent1qJudge1", "tell me a joke"))
    assert "WorkshopPilot" in text or "I didn't" in text


def test_missing_fields_trigger_form_card(bus):
    replies = bus.turn("agent1qJudge1", "Organise a robotics workshop in Jaipur")
    text = joined(replies)
    assert "still need" in text
    cards = [r.card for r in replies if r.card]
    assert cards, "a form card must be offered"
    assert cards[0]["card_kind"] == "form"


def test_brief_creates_event_and_fetches_quotes(bus, harness):
    text = joined(bus.turn("agent1qJudge1", BRIEF))
    event = harness.coordinator_repo.latest_event_for_owner("agent1qJudge1")
    assert event is not None
    assert event["expected_attendees"] == 60
    assert event["budget_inr_paise"] == 2_000_000
    assert event["date"] == "2026-11-15"
    assert event["city"] == "Jaipur"
    assert "Requesting quotes" in text
    text = joined(bus.turn("agent1qJudge1", "status"))
    assert event["status"] == "quoted" or "quoted" in text


def test_options_show_fits_and_blocked(bus, harness):
    bus.turn("agent1qJudge1", BRIEF)
    text = joined(bus.turn("agent1qJudge1", "status"))
    event = harness.coordinator_repo.latest_event_for_owner("agent1qJudge1")
    plan = harness.coordinator_repo.get_plan(event["event_id"], 1)
    assert plan and plan["options"]
    names = {opt["package_name"]: opt for opt in plan["options"]}
    assert names["Starter Lab"]["eligible"] is True
    assert names["Standard Lab"]["eligible"] is True
    assert names["Premium Lab"]["eligible"] is False
    assert "over_budget" in names["Premium Lab"]["reasons"]


def test_full_golden_path_end_to_end(bus, harness):
    sender = "agent1qOrganizer"
    bus.turn(sender, BRIEF)
    text = joined(bus.turn(sender, "status"))
    assert "no active reservation" in text.lower()

    # 1) select Standard Lab
    text = joined(bus.turn(sender, "choose Standard Lab"))
    assert "₹19,200" in text
    code = extract_last_code(text)

    # 2) approve -> reservation -> simulated payment -> invitation draft
    text = joined(bus.turn(sender, f"approve {code}"))
    assert "SIMULATED — NO ON-CHAIN PAYMENT" in text
    assert "₹19,200" in text
    assert "outreach agent" in text
    invitation_code = extract_last_code(text)

    # 3) approve the invitation -> console mode is honestly labelled and the
    #    demo-visible chat codes are surfaced (nothing was emailed)
    text = joined(bus.turn(sender, f"approve {invitation_code}"))
    assert "NOT delivered" in text
    codes = re.findall(r"RSVP ([A-Z0-9]{6}) yes", text)
    assert len(codes) == 2, "console mode should surface one code per test recipient"

    event = harness.coordinator_repo.latest_event_for_owner(sender)
    totals = harness.coordinator_repo.spend_totals(event["event_id"])
    assert totals["committed"] == 1_920_000
    assert totals["proposed"] == 0

    # 4) RSVP via a chat code from the console-mode send
    text = joined(bus.turn(sender, f"RSVP {codes[0]} yes"))
    assert "1 confirmed attendee" in text

    # 5) duplicate RSVP never inflates the count
    text = joined(bus.turn(sender, f"RSVP {codes[0]} yes"))
    assert "already" in text.lower()
    assert harness.outreach_repo.confirmed_count(event["event_id"]) == 1

    # 6) status reports the modes honestly
    text = joined(bus.turn(sender, "status"))
    assert "SIMULATED" in text
    assert "logged" in text.lower()

    # 7) audit timeline exists with several entries
    text = joined(bus.turn(sender, "timeline"))
    assert "payment_verified" in text
    assert "approval_consumed" in text


def test_replan_starter_paid_then_75_amendment_to_standard(bus, harness):
    """The Plan.md money test: Starter paid at 60, attendance -> 75, amendment
    to Standard costs the DELTA; committed expense becomes ₹19,200 (not ₹33,700)."""
    sender = "agent1qAmend"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Starter Lab")
    text = joined(bus.turn(sender, f"approve {last_pending_code(harness, sender)}"))
    assert "SIMULATED" in text
    event = harness.coordinator_repo.latest_event_for_owner(sender)
    assert harness.coordinator_repo.spend_totals(event["event_id"])["committed"] == 1_450_000

    # attendance change
    text = joined(bus.turn(sender, "attendance is now 75"))
    assert "plan version v1 -> v2" in text
    assert "already paid" in text
    assert "delta" in text

    # Starter is now capacity-blocked; Standard fits
    bus.turn(sender, "choose Starter Lab")
    text = joined(bus.turn(sender, "status"))
    event = harness.coordinator_repo.latest_event_for_owner(sender)
    plan = harness.coordinator_repo.get_plan(event["event_id"], 2)
    by_name = {opt["package_name"]: opt for opt in plan["options"]}
    assert by_name["Starter Lab"]["eligible"] is False
    assert "capacity_insufficient" in by_name["Starter Lab"]["reasons"]
    assert by_name["Standard Lab"]["eligible"] is True

    # amendment selection: approval authorizes the INR delta (+₹4,700)
    bus.turn(sender, "choose Standard Lab")
    pending = harness.coordinator_repo.pending_approvals_for_owner(sender)
    assert pending[-1]["amount_inr_paise"] == 470_000

    text = joined(bus.turn(sender, f"approve {pending[-1]['code']}"))
    assert "SIMULATED" in text
    assert harness.coordinator_repo.spend_totals(event["event_id"])["committed"] == 1_920_000

    # the Starter reservation was superseded, the Standard one confirmed
    reservations = harness.coordinator_repo.reservations_for_event(event["event_id"])
    statuses = {r["package_id"]: r["status"] for r in reservations}
    assert statuses["wp-starter-lab"] == "superseded"
    assert statuses["wp-standard-lab"] == "confirmed"


def test_stale_approval_rejected_after_replan(bus, harness):
    sender = "agent1qStale"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Standard Lab")
    stale_code = last_pending_code(harness, sender)
    bus.turn(sender, "attendance is now 75")
    text = joined(bus.turn(sender, f"approve {stale_code}"))
    assert "can no longer be used" in text.lower() or "refused" in text.lower()
    assert "invalidated" in text.lower() or "stale" in text.lower() or "plan v1" in text.lower()


def test_over_budget_selection_rejected(bus, harness):
    sender = "agent1qBudget"
    bus.turn(sender, BRIEF)
    text = joined(bus.turn(sender, "choose Premium Lab"))
    assert "rejected" in text.lower()
    assert "budget" in text.lower()
    assert harness.coordinator_repo.pending_approvals_for_owner(sender) == []


def test_second_package_blocked_while_reservation_active(bus, harness):
    sender = "agent1qDouble"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Standard Lab")
    bus.turn(sender, f"approve {last_pending_code(harness, sender)}")
    text = joined(bus.turn(sender, "choose Starter Lab"))
    assert "rejected" in text.lower()
    assert "already has an active reservation" in text.lower()
    package_pending = [
        a for a in harness.coordinator_repo.pending_approvals_for_owner(sender)
        if a["action_type"] == "reserve_package"
    ]
    assert package_pending == []


def test_per_sender_isolation(bus, harness):
    a, b = "agent1qA", "agent1qB"
    bus.turn(a, BRIEF)
    bus.turn(a, "choose Standard Lab")
    code = last_pending_code(harness, a)
    text = joined(bus.turn(b, f"approve {code}"))
    assert "no approval matches" in text.lower()
    text = joined(bus.turn(b, "status"))
    assert "WorkshopPilot here" in text  # B has no events -> greeting


def test_duplicate_reservation_action_id_is_idempotent(bus, harness):
    sender = "agent1qDup"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Standard Lab")
    bus.turn(sender, f"approve {last_pending_code(harness, sender)}")
    requests = [o.msg for o in bus.log if isinstance(o.msg, ReservationRequest)]
    assert requests
    first = harness.supplier.handle(requests[0]).outgoing[0].msg
    second = harness.supplier.handle(requests[0]).outgoing[0].msg
    assert first.reservation_id == second.reservation_id
    assert second.replayed is True


def test_supplier_timeout_reported_honestly(tmp_path):
    harness = build_harness(tmp_path, agent_reply_timeout_seconds=0.05)
    bus = Bus(harness)
    sender = "agent1qTimeout"
    turn = harness.coordinator.handle_user_text(sender, BRIEF)
    assert turn.outgoing, "a quote request should be pending"
    harness.clock.advance(30)
    text = joined(bus.turn(sender, "status"))
    assert "marked FAILED" in text
    assert "Nothing was assumed" in text


def test_reset_my_demo_is_scoped(bus, harness):
    a, b = "agent1qResetA", "agent1qResetB"
    for sender in (a, b):
        bus.turn(sender, BRIEF)
    text = joined(bus.turn(a, "reset my demo"))
    assert "removed 1 event(s)" in text
    assert harness.coordinator_repo.latest_event_for_owner(b) is not None
    assert harness.coordinator_repo.latest_event_for_owner(a) is None


def test_card_selection_with_smuggled_price_uses_server_quote(bus, harness):
    sender = "agent1qForge"
    bus.turn(sender, BRIEF)
    turn = harness.coordinator.handle_card_response(
        sender,
        {
            "card_protocol_version": "1",
            "selection": '{"type": "select_package", "package_id": "wp-standard-lab", "price_inr_paise": 1}',
        },
    )
    text = joined(bus.deliver(turn))
    assert "₹19,200" in text
    pending = harness.coordinator_repo.pending_approvals_for_owner(sender)
    assert pending and pending[-1]["amount_inr_paise"] == 1_920_000


def test_forged_package_id_rejected(bus, harness):
    sender = "agent1qForge2"
    bus.turn(sender, BRIEF)
    turn = harness.coordinator.handle_card_response(
        sender,
        {
            "card_protocol_version": "1",
            "selection": '{"type": "select_package", "package_id": "wp-vip-lab", "quote_id": "Q-FAKE"}',
        },
    )
    text = joined(bus.deliver(turn))
    assert "not part of the current plan" in text or "rejected" in text.lower()


def test_asi_one_form_json_submission_creates_event(bus, harness):
    """ASI:One form cards return the submitted values as JSON text."""
    sender = "agent1qForm"
    payload = json.dumps(
        {
            "type": "submit_event_form",
            "fields": {
                "title": "Robotics Workshop",
                "city": "Jaipur",
                "date": "2026-11-15",
                "attendees": 60,
                "budget_inr": 20000,
                "equipment": "projector, wifi",
            },
        }
    )
    text = joined(bus.turn(sender, payload))
    event = harness.coordinator_repo.latest_event_for_owner(sender)
    assert event is not None
    assert event["expected_attendees"] == 60
    assert event["budget_inr_paise"] == 2_000_000
    assert event["city"] == "Jaipur"
    assert "Requesting quotes" in text


def test_asi_one_json_card_clicks_are_honoured(bus, harness):
    """Carousel and review CTA selections arrive as a flat JSON text block."""
    sender = "agent1qClicks"
    bus.turn(sender, BRIEF)
    text = joined(
        bus.turn(
            sender,
            json.dumps({"type": "select_package", "package_id": "wp-standard-lab"}),
        )
    )
    code = extract_last_code(text)
    text = joined(
        bus.turn(sender, json.dumps({"type": "approve_package", "approval_code": code}))
    )
    assert "SIMULATED — NO ON-CHAIN PAYMENT" in text
    event = harness.coordinator_repo.latest_event_for_owner(sender)
    assert harness.coordinator_repo.spend_totals(event["event_id"])["committed"] == 1_920_000


def test_planner_mediated_card_click_prose_is_honoured(bus, harness):
    """The ASI:One planner may forward a card click as prose plus the code."""
    sender = "agent1qProse"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Standard Lab")
    code = last_pending_code(harness, sender)
    text = joined(
        bus.turn(sender, f"The user tapped the Approve button for approval {code} on the review card.")
    )
    assert "accepted" in text.lower()
    pending = harness.coordinator_repo.pending_approvals_for_owner(sender)
    assert all(a["code"] != code for a in pending)


def test_console_mode_exposes_codes_but_smtp_does_not():
    invitation = {
        "invitation_id": "I-TEST",
        "plan_version": 1,
        "recipient_refs": ["recipient-1"],
        "status": "sent",
        "provider_message_ids": [],
    }
    request = SendInvitationRequest(
        event_id="E-TEST",
        invitation_id="I-TEST",
        draft_version=1,
        content_hash="hash",
        recipient_set_hash="recipient-hash",
        approval_id="AP-TEST",
        action_id="ACT-TEST",
    )

    class Repo:
        def tokens_for_invitation(self, invitation_id):  # pragma: no cover - smtp test
            raise AssertionError("SMTP mode must not read RSVP tokens for chat")

    smtp = OutreachService(Repo(), SimpleNamespace(email_provider="smtp"), provider=None)
    response = smtp._send_response(
        request, invitation, "completed", rsvp_codes={"recipient-1": "ABC123"}
    )
    assert response.results[0].rsvp_code == ""

    console = OutreachService(
        SimpleNamespace(tokens_for_invitation=lambda invitation_id: []),
        SimpleNamespace(email_provider="console"),
        provider=None,
    )
    response = console._send_response(
        request, invitation, "none", rsvp_codes={"recipient-1": "ABC123"}
    )
    assert response.results[0].rsvp_code == "ABC123"


def test_run_local_keeps_peer_endpoints_local():
    from scripts.run_local import _processes

    specs = {spec["name"]: spec for spec in _processes()}
    for peer in ("supplier", "outreach"):
        assert specs[peer]["env"]["AGENT_MAILBOX"] == "false"
        assert specs[peer]["env"]["AGENT_ENDPOINT"] == ""


def test_bare_yes_with_multiple_pending_asks_for_code(bus, harness):
    sender = "agent1qYes"
    bus.turn(sender, BRIEF)
    bus.turn(sender, "choose Standard Lab")
    bus.turn(sender, "choose Starter Lab")  # second pending approval
    text = joined(bus.turn(sender, "approve"))
    assert "bare 'yes' is not enough" in text or "specific code" in text
