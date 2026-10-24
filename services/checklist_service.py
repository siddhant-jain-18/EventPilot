"""Event checklist derived from persisted state — every item carries its
evidence reference so 'done' is never claimed without a record.
"""

from __future__ import annotations

from typing import Optional


class ChecklistService:
    def __init__(self, repo):
        self.repo = repo

    def build(self, event: dict) -> list[dict]:
        event_id = event["event_id"]
        quotes = self.repo.quotes_for_event(event_id)
        reservations = self.repo.reservations_for_event(event_id)
        active_res = next(
            (r for r in reservations if r["status"] in ("reserved", "payment_pending", "paid", "confirmed")),
            None,
        )
        payments = self.repo.payments_for_reservation(active_res["reservation_id"]) if active_res else []
        settled = [p for p in payments if p["verification_status"] in ("verified", "verified_simulated")]
        invitations = self.repo.invitations_for_event(event_id)
        sent = [i for i in invitations if i["status"] in ("sent", "logged")]
        rsvp_count = self.repo.get_rsvp_count(event_id) or 0

        def item(title: str, done: bool, evidence: str) -> dict:
            return {"item": title, "done": done, "evidence": evidence}

        return [
            item(
                "Event brief captured",
                bool(event.get("title") and event.get("date") and event.get("city")),
                f"event {event_id} plan v{event['current_plan_version']}",
            ),
            item(
                "Supplier quotes received",
                bool(quotes),
                f"{len(quotes)} quote(s) stored",
            ),
            item(
                "Package approved",
                bool(active_res),
                f"reservation {active_res['reservation_id']} ({active_res['status']})" if active_res else "not approved yet",
            ),
            item(
                "Deposit settled (verified or labelled simulation)",
                bool(settled),
                f"payment {settled[-1]['payment_id']} ({settled[-1]['verification_status']})" if settled else "no verified payment",
            ),
            item(
                "Invitation approved and sent/logged",
                bool(sent),
                f"invitation {sent[-1]['invitation_id']} ({sent[-1]['status']})" if sent else "not sent",
            ),
            item(
                "RSVP tracking active",
                rsvp_count > 0,
                f"{rsvp_count} confirmed registration(s) (demo data)",
            ),
        ]

    def narration(self, event: dict) -> str:
        items = self.build(event)
        lines = [f"Checklist for {event['event_id']} — {event['title']}:"]
        for entry in items:
            mark = "[done]" if entry["done"] else "[todo]"
            lines.append(f"- {mark} {entry['item']} — {entry['evidence']}")
        return "\n".join(lines)
