"""Approval service (spec section 10 / Plan.md section 6.2).

An approval is a persisted, single-use authorization record bound to an
immutable snapshot of the exact action it authorizes: plan version, quote,
package, amount, deposit, recipient wallet, or the invitation content hash and
recipient-set hash. Any material change invalidates it. A bare "yes" is not an
approval when more than one action is pending — approvals are consumed by
short human-readable codes (for example ``A-7F3K``).
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from shared.enums import ActionType, ApprovalStatus
from shared.errors import (
    AuthorizationError,
    DuplicateOperationError,
    ExpiredError,
    NotFoundError,
    ValidationError,
)
from shared.time_utils import expiry_iso, is_expired, now_iso
from shared.validation import canonical_hash, validate_paise
from storage.database import new_id

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _new_code(existing: set[str]) -> str:
    for _ in range(20):
        code = "A-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(5))
        if code not in existing:
            return code
    raise RuntimeError("could not allocate a unique approval code")


class ApprovalService:
    def __init__(self, repo, ttl_seconds: int = 900):
        self.repo = repo
        self.ttl_seconds = ttl_seconds

    # -- creation ------------------------------------------------------------

    def create_package_approval(
        self,
        *,
        event: dict,
        quote: dict,
        amount_inr_paise: int | None = None,
    ) -> dict:
        """Approve-one-exact-package snapshot. The price comes from the stored
        quote record, never from the client or the card. For amendments the
        authorized amount is the INR DELTA (committed expense is replaced,
        not stacked)."""
        amount = validate_paise(
            amount_inr_paise if amount_inr_paise is not None else quote["price_inr_paise"],
            field="price",
        )
        payload: dict[str, Any] = {
            "action": ActionType.RESERVE_PACKAGE.value,
            "event_id": event["event_id"],
            "plan_version": event["current_plan_version"],
            "quote_id": quote["quote_id"],
            "package_id": quote["package_id"],
            "package_name": quote["package_name"],
            "capacity": quote["capacity"],
            "attendee_count": quote["attendee_count"],
            "price_inr_paise": amount,
            "currency": "INR",
            "deposit_fet_base": quote["deposit_fet_base"],
            "asset": "atestfet",
            "recipient_wallet": quote.get("recipient_wallet", ""),
            "quote_expires_at": quote["expires_at"],
            "amendment_of_reservation_id": quote.get("amendment_of_reservation_id"),
        }
        return self._persist(event, payload, amount_inr_paise=amount)

    def create_invitation_approval(
        self,
        *,
        event: dict,
        invitation_id: str,
        draft_version: int,
        content_hash: str,
        recipient_set_hash: str,
        recipient_count: int,
    ) -> dict:
        payload: dict[str, Any] = {
            "action": ActionType.SEND_INVITATION.value,
            "event_id": event["event_id"],
            "plan_version": event["current_plan_version"],
            "invitation_id": invitation_id,
            "draft_version": draft_version,
            "content_hash": content_hash,
            "recipient_set_hash": recipient_set_hash,
            "recipient_count": recipient_count,
        }
        return self._persist(event, payload, amount_inr_paise=None)

    def _persist(self, event: dict, payload: dict, *, amount_inr_paise: int | None) -> dict:
        row = {
            "approval_id": new_id("AP"),
            "code": _new_code(self._existing_codes()),
            "event_id": event["event_id"],
            "owner_sender": event["owner_sender"],
            "plan_version": event["current_plan_version"],
            "action_type": payload["action"],
            "payload": payload,
            "action_payload_hash": canonical_hash(payload),
            "amount_inr_paise": amount_inr_paise,
            "recipient_set_hash": payload.get("recipient_set_hash"),
            "status": ApprovalStatus.PENDING.value,
            "created_at": now_iso(),
            "expires_at": expiry_iso(self.ttl_seconds),
            "consumed_at": None,
        }
        self.repo.save_approval(row)
        return row

    def _existing_codes(self) -> set[str]:
        rows = self.repo.conn.execute("SELECT code FROM approvals").fetchall()
        return {r["code"] for r in rows}

    # -- consumption ------------------------------------------------------------

    def consume(
        self,
        *,
        code_or_id: str,
        owner_sender: str,
        event: dict,
        expected_action: ActionType,
        expected_payload: Optional[dict] = None,
    ) -> dict:
        """Validate every binding and mark the approval consumed.

        Rejects: unknown approval, wrong owner, replayed, expired, superseded
        plan version, or any mismatch with the expected payload snapshot.
        """
        approval: Optional[dict] = None
        value = (code_or_id or "").strip().upper()
        if value.startswith("A-") and len(value) <= 8:
            approval = self.repo.get_approval_by_code(value, owner_sender)
            if approval is None:
                approval_id = value  # allow full ids as a fallback path
                try:
                    approval = self.repo.get_approval(approval_id, owner_sender)
                except NotFoundError:
                    approval = None
        else:
            approval = self.repo.get_approval(value, owner_sender)
        if approval is None:
            raise NotFoundError(
                f"no approval found for '{code_or_id}' — check the code shown with the request"
            )
        if approval["event_id"] != event["event_id"]:
            raise AuthorizationError("approval belongs to a different event")
        if approval["status"] == ApprovalStatus.CONSUMED.value:
            raise DuplicateOperationError(
                f"approval {approval['code']} was already used at {approval['consumed_at']}"
            )
        if approval["status"] in (ApprovalStatus.INVALIDATED.value, ApprovalStatus.EXPIRED.value, ApprovalStatus.REJECTED.value):
            raise ValidationError(
                f"approval {approval['code']} is {approval['status']} and can no longer be used"
            )
        if approval["status"] != ApprovalStatus.PENDING.value:
            raise ValidationError(f"approval {approval['code']} is not pending")
        if is_expired(approval["expires_at"]):
            self.repo.update_approval_status(approval["approval_id"], ApprovalStatus.EXPIRED.value)
            raise ExpiredError(
                f"approval {approval['code']} expired at {approval['expires_at']} — request a fresh one"
            )
        if approval["plan_version"] != event["current_plan_version"]:
            raise ValidationError(
                f"approval {approval['code']} was bound to plan version "
                f"{approval['plan_version']} but the event is on version "
                f"{event['current_plan_version']} — stale approval"
            )
        if approval["action_type"] != expected_action.value:
            raise ValidationError("approval action type mismatch")
        recomputed = canonical_hash(approval["payload"])
        if recomputed != approval["action_payload_hash"]:
            raise AuthorizationError("approval payload hash mismatch — snapshot was tampered with")
        if expected_payload is not None and approval["payload"] != expected_payload:
            raise ValidationError(
                "approval does not match the action being executed (altered package, "
                "amount, recipients or plan)"
            )
        consumed_at = now_iso()
        self.repo.update_approval_status(
            approval["approval_id"], ApprovalStatus.CONSUMED.value, consumed_at
        )
        approval["status"] = ApprovalStatus.CONSUMED.value
        approval["consumed_at"] = consumed_at
        return approval

    # -- invalidation -----------------------------------------------------------

    def invalidate_pending_for_event(self, event_id: str) -> int:
        """Any material change invalidates outstanding approvals."""
        return self.repo.invalidate_pending_approvals(event_id)
