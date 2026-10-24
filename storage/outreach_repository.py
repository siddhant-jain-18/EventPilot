"""Outreach-side repositories: invitations (with recipient data, which NEVER
leaves this database), signed RSVP tokens and RSVP registrations.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from shared.errors import DuplicateOperationError, NotFoundError
from shared.time_utils import now_iso
from storage.database import new_id


def _j(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _jl(raw: str) -> list:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OutreachRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- invitations ----------------------------------------------------------

    def save_invitation(self, i: dict) -> None:
        try:
            self.conn.execute(
                "INSERT INTO invitations (invitation_id, event_id, plan_version, draft_version,"
                " subject, body, content_hash, recipient_refs, recipient_emails, recipient_count,"
                " recipient_set_hash, approval_id, status, provider_message_ids, idempotency_key,"
                " mode, created_at, sent_at, failure_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i["invitation_id"], i["event_id"], i["plan_version"], i["draft_version"],
                    i["subject"], i["body"], i["content_hash"], _j(i.get("recipient_refs") or []),
                    _j(i.get("recipient_emails") or []), i["recipient_count"],
                    i["recipient_set_hash"], i.get("approval_id"), i.get("status", "draft"),
                    _j(i.get("provider_message_ids") or []), i.get("idempotency_key"),
                    i.get("mode", "console"), i["created_at"], i.get("sent_at"), i.get("failure_reason"),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as err:
            raise DuplicateOperationError(
                f"invitation with idempotency key {i.get('idempotency_key')} already exists"
            ) from err

    def get_invitation(self, invitation_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM invitations WHERE invitation_id = ?", (invitation_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"invitation {invitation_id} not found")
        return self._invitation(row)

    def latest_invitation_for_event(self, event_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM invitations WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        return self._invitation(row) if row else None

    def update_invitation(self, invitation_id: str, **fields: object) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        values = [
            _j(v) if k in ("provider_message_ids", "recipient_refs", "recipient_emails") else v
            for k, v in fields.items()
        ]
        self.conn.execute(
            f"UPDATE invitations SET {sets} WHERE invitation_id = ?", (*values, invitation_id)
        )
        self.conn.commit()

    def mark_older_invitations_stale(self, event_id: str, keep_invitation_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE invitations SET status = 'stale' WHERE event_id = ? AND invitation_id != ?"
            " AND status IN ('sent', 'logged', 'draft', 'awaiting_approval', 'approved', 'failed', 'unknown')",
            (event_id, keep_invitation_id),
        )
        self.conn.commit()
        return cur.rowcount

    @staticmethod
    def _invitation(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["recipient_refs"] = _jl(data["recipient_refs"])
        data["recipient_emails"] = _jl(data["recipient_emails"])
        data["provider_message_ids"] = _jl(data["provider_message_ids"])
        return data

    # -- RSVP tokens --------------------------------------------------------------

    def save_token(self, t: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO rsvp_tokens (token_id, event_id, invitation_id, token_hash,"
            " rsvp_code, code_hash, recipient_ref, expires_at, used_at, revoked, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t["token_id"], t["event_id"], t["invitation_id"], t["token_hash"],
                t["rsvp_code"], t["code_hash"], t["recipient_ref"], t["expires_at"],
                t.get("used_at"), t.get("revoked", 0), t["created_at"],
            ),
        )
        self.conn.commit()

    def get_token_by_hash(self, token_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rsvp_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        return dict(row) if row else None

    def get_token_by_code_hash(self, code_hash: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM rsvp_tokens WHERE code_hash = ?", (code_hash,)
        ).fetchone()
        return dict(row) if row else None

    def mark_token_used(self, token_id: str) -> None:
        self.conn.execute(
            "UPDATE rsvp_tokens SET used_at = ? WHERE token_id = ?", (now_iso(), token_id)
        )
        self.conn.commit()

    # -- RSVPs -----------------------------------------------------------------

    def record_rsvp(self, event_id: str, token_id: str, attendee_reference: str, status: str = "confirmed") -> dict:
        ts = now_iso()
        rsvp_id = f"RSVP-{token_id[-8:]}-{hashlib.sha1(ts.encode()).hexdigest()[:6].upper()}"
        try:
            self.conn.execute(
                "INSERT INTO rsvps (rsvp_id, event_id, token_id, attendee_reference, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rsvp_id, event_id, token_id, attendee_reference, status, ts, ts),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as err:
            raise DuplicateOperationError(
                "This invitation has already been used to register."
            ) from err
        row = self.conn.execute("SELECT * FROM rsvps WHERE rsvp_id = ?", (rsvp_id,)).fetchone()
        return dict(row)

    def confirmed_count(self, event_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM rsvps WHERE event_id = ? AND status = 'confirmed'",
            (event_id,),
        ).fetchone()
        return int(row["n"])

    def audit(self, action: str, entity_type: str, entity_id: str, result: str, event_id: str | None = None, metadata: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (audit_id, event_id, actor, action, entity_type, entity_id, result, timestamp, safe_metadata)"
            " VALUES (?, ?, 'outreach', ?, ?, ?, ?, ?, ?)",
            (new_id("AU"), event_id, action, entity_type, entity_id, result, now_iso(), _j(metadata or {})),
        )
        self.conn.commit()

    # -- reset -------------------------------------------------------------

    def reset_demo(self) -> None:
        for table in ("invitations", "rsvp_tokens", "rsvps", "audit_log"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
