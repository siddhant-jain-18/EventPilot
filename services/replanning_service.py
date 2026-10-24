"""Replanning semantics (spec section 9 'Change handling' / Plan.md 6.3).

Attendance changes NEVER auto-spend or auto-send. The service:
1. validates the new count,
2. bumps the plan version,
3. invalidates all pending approvals,
4. re-evaluates capacity/budget eligibility,
5. decides the correct amendment path for an existing reservation:
   - not approved yet            -> just re-quote,
   - reserved but not paid       -> old reservation must be superseded first,
   - paid/confirmed              -> supplier-issued amendment quote for the
                                    deposit DELTA; committed expense becomes
                                    the new package price (never stacked),
   - nothing fits                -> plain statement + options; budget is never
                                    raised silently.
6. marks previously sent invitations stale so a fresh approval is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from shared.errors import ValidationError
from shared.models import EligibilityCheck
from shared.validation import format_inr


@dataclass
class ReplanOutcome:
    event_id: str
    old_plan_version: int
    new_plan_version: int
    old_attendees: int
    new_attendees: int
    invalidated_approvals: int
    amendment_required: bool = False
    amendment_of_reservation_id: Optional[str] = None
    supersede_required: bool = False
    supersede_reservation_id: Optional[str] = None
    invitation_stale_required: bool = False
    explanation: list[str] = field(default_factory=list)


class ReplanningService:
    def __init__(self, repo, approval_service):
        self.repo = repo
        self.approvals = approval_service

    def plan_change(self, event: dict, new_attendees: int) -> ReplanOutcome:
        if not isinstance(new_attendees, int) or isinstance(new_attendees, bool):
            raise ValidationError("attendee count must be an integer")
        if new_attendees < 1 or new_attendees > 10000:
            raise ValidationError(f"invalid attendee count: {new_attendees}")
        old_count = event["expected_attendees"]
        if new_attendees == old_count:
            raise ValidationError("attendance is unchanged; nothing to replan")

        old_version = event["current_plan_version"]
        new_version = old_version + 1

        invalidated = self.approvals.invalidate_pending_for_event(event["event_id"])

        outcome = ReplanOutcome(
            event_id=event["event_id"],
            old_plan_version=old_version,
            new_plan_version=new_version,
            old_attendees=old_count,
            new_attendees=new_attendees,
            invalidated_approvals=invalidated,
        )
        outcome.explanation.append(
            f"Attendance changed {old_count} -> {new_attendees}: plan version "
            f"v{old_version} -> v{new_version}, {invalidated} pending approval(s) invalidated."
        )

        reservations = self.repo.reservations_for_event(event["event_id"])
        active = next(
            (r for r in reservations if r["status"] in ("reserved", "payment_pending", "paid", "confirmed")),
            None,
        )
        if active is not None:
            if active["status"] in ("reserved", "payment_pending"):
                outcome.supersede_required = True
                outcome.supersede_reservation_id = active["reservation_id"]
                outcome.explanation.append(
                    f"Reservation {active['reservation_id']} exists but is not paid; it will be "
                    f"marked superseded once you approve the new plan. No money has moved yet."
                )
            else:
                outcome.amendment_required = True
                outcome.amendment_of_reservation_id = active["reservation_id"]
                totals = self.repo.spend_totals(event["event_id"])
                outcome.explanation.append(
                    f"Reservation {active['reservation_id']} is already paid "
                    f"({format_inr(totals['committed'])} committed). A package change needs a "
                    f"supplier amendment quote for the deposit delta and fresh approval; the "
                    f"committed expense becomes the new package price — it is not stacked."
                )

        if self.repo.invitations_for_event(event["event_id"]):
            outcome.invitation_stale_required = True
            outcome.explanation.append(
                "Existing invitations will be marked stale; sending an updated invitation "
                "requires a new approval — nothing is auto-sent."
            )
        return outcome

    @staticmethod
    def explain_eligibility_change(
        old_checks: list[EligibilityCheck], new_checks: list[EligibilityCheck]
    ) -> list[str]:
        lines: list[str] = []
        new_by_id = {c.package_id: c for c in new_checks}
        for old in old_checks:
            fresh = new_by_id.get(old.package_id)
            if fresh is None:
                continue
            if old.eligible and not fresh.eligible:
                reasons = ", ".join(fresh.reasons)
                lines.append(f"{old.package_name} no longer fits ({reasons}).")
            elif not old.eligible and fresh.eligible:
                lines.append(f"{old.package_name} now fits.")
        return lines
