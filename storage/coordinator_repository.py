"""Coordinator-side repositories. Every owner-scoped query filters on
``owner_sender`` so two organizers (or judges) can never see or mutate each
other's state (per-sender isolation, Plan.md section 10).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from shared.enums import ApprovalStatus, QuoteStatus
from shared.errors import NotFoundError
from shared.time_utils import now_iso
from storage.database import new_id


def _j(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _jl(raw: str, default: object = None) -> object:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


class CoordinatorRepository:
    """Single facade over the coordinator SQLite database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- events ------------------------------------------------------------

    def create_event(self, **fields: object) -> dict:
        ts = now_iso()
        row = {
            "event_id": fields.get("event_id") or new_id("E"),
            "owner_sender": fields["owner_sender"],
            "title": fields.get("title") or "Untitled event",
            "event_type": fields.get("event_type") or "workshop",
            "date": fields["date"],
            "timezone": fields.get("timezone") or "Asia/Kolkata",
            "city": fields["city"],
            "expected_attendees": fields["expected_attendees"],
            "budget_inr_paise": fields["budget_inr_paise"],
            "equipment_requirements": _j(fields.get("equipment_requirements") or []),
            "status": fields.get("status") or "draft",
            "current_plan_version": fields.get("current_plan_version", 1),
            "created_at": ts,
            "updated_at": ts,
        }
        cols = ", ".join(row.keys())
        marks = ", ".join("?" for _ in row)
        self.conn.execute(f"INSERT INTO events ({cols}) VALUES ({marks})", tuple(row.values()))
        self.conn.commit()
        return self.get_event(row["event_id"], row["owner_sender"])  # type: ignore[arg-type]

    def get_event(self, event_id: str, owner_sender: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM events WHERE event_id = ? AND owner_sender = ?",
            (event_id, owner_sender),
        ).fetchone()
        if not row:
            raise NotFoundError(f"event {event_id} not found for this organizer")
        return self._event(row)

    def get_event_any_owner(self, event_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._event(row) if row else None

    def latest_event_for_owner(self, owner_sender: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM events WHERE owner_sender = ? ORDER BY created_at DESC LIMIT 1",
            (owner_sender,),
        ).fetchone()
        return self._event(row) if row else None

    def list_events_for_owner(self, owner_sender: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE owner_sender = ? ORDER BY created_at DESC",
            (owner_sender,),
        ).fetchall()
        return [self._event(r) for r in rows]

    def update_event(self, event_id: str, owner_sender: str, **fields: object) -> dict:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [
            _j(v) if k == "equipment_requirements" else v for k, v in fields.items()
        ]
        cur = self.conn.execute(
            f"UPDATE events SET {sets} WHERE event_id = ? AND owner_sender = ?",
            (*values, event_id, owner_sender),
        )
        if cur.rowcount == 0:
            raise NotFoundError(f"event {event_id} not found for this organizer")
        self.conn.commit()
        return self.get_event(event_id, owner_sender)

    @staticmethod
    def _event(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["equipment_requirements"] = _jl(data["equipment_requirements"], [])
        return data

    # -- plans ---------------------------------------------------------------

    def save_plan(self, event_id: str, plan_version: int, attendee_count: int, options: list[dict]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO plans (event_id, plan_version, attendee_count, options_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (event_id, plan_version, attendee_count, _j(options), now_iso()),
        )
        self.conn.commit()

    def get_plan(self, event_id: str, plan_version: int) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM plans WHERE event_id = ? AND plan_version = ?",
            (event_id, plan_version),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["options"] = _jl(data["options_json"], [])
        return data

    # -- quotes ---------------------------------------------------------------

    def save_quote(self, q: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO quotes (quote_id, event_id, plan_version, supplier_id, package_id,"
            " package_name, capacity, attendee_count, price_inr_paise, currency, deposit_fet_base,"
            " inclusions, status, created_at, expires_at, amendment_of_reservation_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                q["quote_id"], q["event_id"], q["plan_version"], q["supplier_id"],
                q["package_id"], q["package_name"], q["capacity"], q["attendee_count"],
                q["price_inr_paise"], q.get("currency", "INR"), q["deposit_fet_base"],
                _j(q.get("inclusions") or []), q.get("status", "active"),
                q["created_at"], q["expires_at"], q.get("amendment_of_reservation_id"),
            ),
        )
        self.conn.commit()

    def get_quote(self, quote_id: str, event_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE quote_id = ? AND event_id = ?",
            (quote_id, event_id),
        ).fetchone()
        if not row:
            raise NotFoundError(f"quote {quote_id} not found for this event")
        data = dict(row)
        data["inclusions"] = _jl(data["inclusions"], [])
        return data

    def quotes_for_event(self, event_id: str, plan_version: Optional[int] = None) -> list[dict]:
        if plan_version is None:
            rows = self.conn.execute(
                "SELECT * FROM quotes WHERE event_id = ? ORDER BY created_at", (event_id,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM quotes WHERE event_id = ? AND plan_version = ? ORDER BY created_at",
                (event_id, plan_version),
            ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["inclusions"] = _jl(data["inclusions"], [])
            out.append(data)
        return out

    def set_quote_status(self, quote_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE quotes SET status = ? WHERE quote_id = ?", (status, quote_id)
        )
        self.conn.commit()

    # -- approvals -------------------------------------------------------------

    def save_approval(self, a: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO approvals (approval_id, code, event_id, owner_sender, plan_version,"
            " action_type, payload_json, action_payload_hash, amount_inr_paise, recipient_set_hash,"
            " status, created_at, expires_at, consumed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                a["approval_id"], a["code"], a["event_id"], a["owner_sender"], a["plan_version"],
                a["action_type"], _j(a["payload"]), a["action_payload_hash"], a.get("amount_inr_paise"),
                a.get("recipient_set_hash"), a.get("status", "pending"), a["created_at"],
                a["expires_at"], a.get("consumed_at"),
            ),
        )
        self.conn.commit()

    def get_approval(self, approval_id: str, owner_sender: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ? AND owner_sender = ?",
            (approval_id, owner_sender),
        ).fetchone()
        if not row:
            raise NotFoundError("approval not found for this organizer")
        return self._approval(row)

    def get_approval_by_code(self, code: str, owner_sender: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM approvals WHERE code = ? AND owner_sender = ?",
            (code, owner_sender),
        ).fetchone()
        return self._approval(row) if row else None

    def pending_approvals_for_owner(self, owner_sender: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM approvals WHERE owner_sender = ? AND status = ? ORDER BY created_at DESC",
            (owner_sender, ApprovalStatus.PENDING.value),
        ).fetchall()
        return [self._approval(r) for r in rows]

    def update_approval_status(self, approval_id: str, status: str, consumed_at: Optional[str] = None) -> None:
        self.conn.execute(
            "UPDATE approvals SET status = ?, consumed_at = COALESCE(?, consumed_at) WHERE approval_id = ?",
            (status, consumed_at, approval_id),
        )
        self.conn.commit()

    def invalidate_pending_approvals(self, event_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE approvals SET status = ? WHERE event_id = ? AND status = ?",
            (ApprovalStatus.INVALIDATED.value, event_id, ApprovalStatus.PENDING.value),
        )
        self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _approval(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["payload"] = _jl(data["payload_json"], {})
        return data

    # -- reservations / payments -------------------------------------------------

    def save_reservation(self, r: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reservations (reservation_id, event_id, plan_version, quote_id,"
            " package_id, status, payment_mode, action_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["reservation_id"], r["event_id"], r["plan_version"], r["quote_id"],
                r["package_id"], r["status"], r.get("payment_mode"), r["action_id"],
                r["created_at"], r["updated_at"],
            ),
        )
        self.conn.commit()

    def get_reservation(self, reservation_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
        ).fetchone()
        return dict(row) if row else None

    def reservations_for_event(self, event_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reservations WHERE event_id = ? ORDER BY created_at", (event_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update_reservation(self, reservation_id: str, **fields: object) -> None:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE reservations SET {sets} WHERE reservation_id = ?",
            (*fields.values(), reservation_id),
        )
        self.conn.commit()

    def save_payment(self, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO payments (payment_id, reservation_id, quote_id, event_id,"
            " expected_amount_fet_base, asset, network, recipient_address, transaction_id,"
            " verification_status, payment_mode, amount_inr_paise, created_at, verified_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["payment_id"], p["reservation_id"], p["quote_id"], p["event_id"],
                p["expected_amount_fet_base"], p.get("asset", "atestfet"), p.get("network", "simulated"),
                p.get("recipient_address", ""), p.get("transaction_id"), p.get("verification_status", "not_started"),
                p.get("payment_mode", "simulated"), p.get("amount_inr_paise"), p["created_at"], p.get("verified_at"),
            ),
        )
        self.conn.commit()

    def get_payment(self, payment_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_payment(self, payment_id: str, **fields: object) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE payments SET {sets} WHERE payment_id = ?",
            (*fields.values(), payment_id),
        )
        self.conn.commit()

    def payments_for_reservation(self, reservation_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM payments WHERE reservation_id = ? ORDER BY created_at", (reservation_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- invitations mirror / rsvp counts ----------------------------------------

    def save_invitation_record(self, i: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO invitations (invitation_id, event_id, plan_version, draft_version,"
            " subject, body_preview, content_hash, recipient_set_hash, recipient_count, approval_id,"
            " status, provider_message_ids, mode, created_at, sent_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i["invitation_id"], i["event_id"], i["plan_version"], i["draft_version"],
                i["subject"], i.get("body_preview", ""), i["content_hash"],
                i["recipient_set_hash"], i["recipient_count"], i.get("approval_id"),
                i.get("status", "draft"), _j(i.get("provider_message_ids") or []),
                i.get("mode", "console"), i["created_at"], i.get("sent_at"),
            ),
        )
        self.conn.commit()

    def invitations_for_event(self, event_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM invitations WHERE event_id = ? ORDER BY created_at", (event_id,)
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            data["provider_message_ids"] = _jl(data["provider_message_ids"], [])
            out.append(data)
        return out

    def update_invitation_record(self, invitation_id: str, **fields: object) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [
            _j(v) if k == "provider_message_ids" else v for k, v in fields.items()
        ]
        self.conn.execute(
            f"UPDATE invitations SET {sets} WHERE invitation_id = ?",
            (*values, invitation_id),
        )
        self.conn.commit()

    def set_rsvp_count(self, event_id: str, confirmed_count: int) -> None:
        self.conn.execute(
            "INSERT INTO rsvp_counts (event_id, confirmed_count, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(event_id) DO UPDATE SET confirmed_count = ?, updated_at = ?",
            (event_id, confirmed_count, now_iso(), confirmed_count, now_iso()),
        )
        self.conn.commit()

    def get_rsvp_count(self, event_id: str) -> Optional[int]:
        row = self.conn.execute(
            "SELECT confirmed_count FROM rsvp_counts WHERE event_id = ?", (event_id,)
        ).fetchone()
        return int(row["confirmed_count"]) if row else None

    # -- spend ledger ---------------------------------------------------------------

    def add_spend_entry(self, event_id: str, entry_type: str, amount_inr_paise: int, ref: str = "", note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO spend_ledger (event_id, entry_type, amount_inr_paise, ref, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (event_id, entry_type, amount_inr_paise, ref, note, now_iso()),
        )
        self.conn.commit()

    def spend_totals(self, event_id: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT entry_type, amount_inr_paise FROM spend_ledger WHERE event_id = ?",
            (event_id,),
        ).fetchall()
        committed = proposed = 0
        for row in rows:
            if row["entry_type"] == "committed":
                committed += row["amount_inr_paise"]
            elif row["entry_type"] == "committed_reversal":
                committed -= row["amount_inr_paise"]
            elif row["entry_type"] == "proposed":
                proposed += row["amount_inr_paise"]
            elif row["entry_type"] == "proposed_reversal":
                proposed -= row["amount_inr_paise"]
        return {"committed": committed, "proposed": proposed}

    # -- outbound request tracking (timeouts / idempotency) -----------------------------

    def record_outbound(self, request_id: str, role: str, kind: str, event_id: str, owner_sender: str = "") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO outbound_requests (request_id, role, kind, event_id, owner_sender, created_at, status)"
            " VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (request_id, role, kind, event_id, owner_sender, now_iso()),
        )
        self.conn.commit()

    def get_outbound(self, request_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM outbound_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    def complete_outbound(self, request_id: str) -> None:
        self.conn.execute(
            "UPDATE outbound_requests SET status = 'completed', completed_at = ? WHERE request_id = ?",
            (now_iso(), request_id),
        )
        self.conn.commit()

    def pending_outbound(self, role: Optional[str] = None) -> list[dict]:
        if role:
            rows = self.conn.execute(
                "SELECT * FROM outbound_requests WHERE status = 'pending' AND role = ?", (role,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM outbound_requests WHERE status = 'pending'"
            ).fetchall()
        return [dict(r) for r in rows]

    def fail_outbound(self, request_id: str) -> None:
        self.conn.execute(
            "UPDATE outbound_requests SET status = 'failed', completed_at = ? WHERE request_id = ?",
            (now_iso(), request_id),
        )
        self.conn.commit()

    # -- reset -------------------------------------------------------------

    def reset_owner(self, owner_sender: str) -> int:
        """Delete every record owned by one sender (scoped 'reset my demo')."""
        events = self.list_events_for_owner(owner_sender)
        event_ids = [e["event_id"] for e in events]
        for event_id in event_ids:
            for table in (
                "plans", "quotes", "approvals", "reservations", "payments",
                "invitations", "rsvp_counts", "spend_ledger", "outbound_requests",
                "audit_log",
            ):
                self.conn.execute(f"DELETE FROM {table} WHERE event_id = ?", (event_id,))
        self.conn.execute("DELETE FROM events WHERE owner_sender = ?", (owner_sender,))
        self.conn.execute("DELETE FROM outbound_requests WHERE owner_sender = ?", (owner_sender,))
        self.conn.commit()
        return len(event_ids)
