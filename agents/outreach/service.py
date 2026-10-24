"""Outreach service (spec section 4.3) — invitations, safe sending, RSVP.

Owns invitation drafts, the recipient allowlist, send records and RSVP data.
Attendee e-mail addresses NEVER leave this service: chat replies and agent
messages carry recipient references and counts only.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from shared.enums import (
    ApprovalStatus,
    ExternalEffect,
    InvitationStatus,
    RsvpStatus,
)
from shared.errors import (
    AuthorizationError,
    DuplicateOperationError,
    EventPilotError,
    ExpiredError,
    NotFoundError,
    ValidationError,
)
from shared.logging_config import get_logger
from shared.messages import (
    InvitationDraftRequest,
    InvitationDraftResponse,
    RsvpCountRequest,
    RsvpCountResponse,
    RsvpSubmitRequest,
    RsvpSubmitResponse,
    SendInvitationRequest,
    SendInvitationResponse,
    SendResultLine,
)
from shared.runtime import Outbound, TurnResult
from shared.time_utils import expiry_iso, now_iso
from shared.validation import (
    content_hash,
    validate_allowlisted_email,
)
from rsvp_web.tokens import hash_code, hash_token, issue_rsvp_token
from storage.database import new_id

logger = get_logger("outreach")


def _template_invitation(req: InvitationDraftRequest) -> tuple[str, str]:
    subject = f"Invitation: {req.title} on {req.date} in {req.city}"
    body = (
        f"Hello,\n\n"
        f"You are invited to the {req.event_type} \"{req.title}\".\n\n"
        f"Date: {req.date} ({req.timezone})\n"
        f"City: {req.city}\n"
        f"Expected attendance: {req.expected_attendees}\n"
        + (f"Note: {req.note}\n" if req.note else "")
        + "\nPlease confirm your attendance with your personal RSVP link or code below.\n\n"
        "— EventPilot (demo; inventory and prices are demo data)"
    )
    return subject, body


class OutreachService:
    def __init__(
        self,
        repo,
        settings,
        provider,
        *,
        audit=None,
        draft_fn: Optional[Callable[[InvitationDraftRequest], tuple[str, str] | None]] = None,
        now_fn: Callable[[], Any] = now_iso,
    ):
        self.repo = repo
        self.settings = settings
        self.provider = provider
        self.audit = audit
        self.draft_fn = draft_fn  # optional LLM drafter; falls back to template
        self.now_fn = now_fn

    # ------------------------------------------------------------------

    def handle(self, msg: Any) -> TurnResult:
        result = TurnResult()
        if isinstance(msg, InvitationDraftRequest):
            self._on_draft_request(msg, result)
        elif isinstance(msg, SendInvitationRequest):
            self._on_send_request(msg, result)
        elif isinstance(msg, RsvpCountRequest):
            self._on_count_request(msg, result)
        elif isinstance(msg, RsvpSubmitRequest):
            self._on_rsvp_submit(msg, result)
        else:
            logger.warning("outreach received unsupported message type %s", type(msg).__name__)
        return result

    # -- drafts ----------------------------------------------------------

    def _on_draft_request(self, msg: InvitationDraftRequest, result: TurnResult) -> None:
        try:
            drafted = self.draft_fn(msg) if self.draft_fn else None
            subject, body = drafted or _template_invitation(msg)
            allowlist = self.settings.allowed_test_recipients
            if not allowlist:
                raise ValidationError(
                    "ALLOWED_TEST_RECIPIENTS is empty; refusing to draft for an unknown recipient set"
                )
            refs = [f"recipient-{n + 1}" for n in range(len(allowlist))]
            recipient_set_hash = content_hash("|".join(sorted(refs)))
            ts = self.now_fn()
            latest = self.repo.latest_invitation_for_event(msg.event_id)
            superseded_id = None
            if latest is not None and latest["draft_version"] >= 1:
                next_version = latest["draft_version"] + 1
                superseded_id = latest["invitation_id"]
            else:
                next_version = 1
            invitation = {
                "invitation_id": new_id("I"),
                "event_id": msg.event_id,
                "plan_version": msg.plan_version or 1,
                "draft_version": next_version,
                "subject": subject,
                "body": body,
                "content_hash": content_hash(subject + "\n" + body),
                "recipient_refs": refs,
                "recipient_emails": list(allowlist),
                "recipient_count": len(refs),
                "recipient_set_hash": recipient_set_hash,
                "status": InvitationStatus.DRAFT.value,
                "mode": self.settings.email_provider,
                "created_at": ts,
            }
            self.repo.save_invitation(invitation)
            if superseded_id:
                self.repo.mark_older_invitations_stale(msg.event_id, invitation["invitation_id"])
            response = InvitationDraftResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                plan_version=msg.plan_version,
                invitation_id=invitation["invitation_id"],
                draft_version=next_version,
                subject=subject,
                body=body,
                content_hash=invitation["content_hash"],
                recipient_count=len(refs),
                recipient_set_hash=recipient_set_hash,
                superseded_invitation_id=superseded_id,
                side_effect=ExternalEffect.NONE.value,
            )
            self.repo.audit(
                "invitation_drafted",
                "invitation",
                invitation["invitation_id"],
                "ok",
                event_id=msg.event_id,
                metadata={"draft_version": next_version, "recipients": len(refs)},
            )
        except EventPilotError as err:
            response = InvitationDraftResponse.failure(
                msg.request_id, err, correlation_id=msg.correlation_id, event_id=msg.event_id
            )
            self.repo.audit(
                "invitation_draft_rejected", "invitation", msg.request_id, err.code, event_id=msg.event_id
            )
        result.send("coordinator", response)

    # -- sending -----------------------------------------------------------

    def _on_send_request(self, msg: SendInvitationRequest, result: TurnResult) -> None:
        try:
            invitation = self.repo.get_invitation(msg.invitation_id)
            if invitation["event_id"] != msg.event_id:
                raise AuthorizationError("invitation belongs to another event")
            if invitation["draft_version"] != msg.draft_version:
                raise ValidationError("draft version changed after approval")
            if invitation["content_hash"] != msg.content_hash:
                raise ValidationError("invitation content changed after approval")
            if invitation["recipient_set_hash"] != msg.recipient_set_hash:
                raise ValidationError("recipient set changed after approval")
            if invitation["status"] in (InvitationStatus.SENT.value, InvitationStatus.LOGGED.value):
                # idempotent replay: return the recorded outcome, never resend
                response = self._send_response(
                    msg,
                    invitation,
                    ExternalEffect.COMPLETED.value
                    if invitation["status"] == InvitationStatus.SENT.value
                    else ExternalEffect.NONE.value,
                )
                result.send("coordinator", response)
                return
            if invitation["status"] in (InvitationStatus.STALE.value,):
                raise ValidationError("invitation is stale (a newer plan exists); draft a fresh one")

            allowlist = self.settings.allowed_test_recipients
            emails = invitation["recipient_emails"]
            if len(emails) != len(invitation["recipient_refs"]) or not all(
                validate_allowlisted_email(e, allowlist) for e in emails
            ):
                raise AuthorizationError(
                    "recipient set is not fully allowlisted; refusing to send"
                )

            self.repo.update_invitation(
                invitation["invitation_id"], status=InvitationStatus.SENDING.value
            )
            from integrations.email_client import OutgoingEmail

            messages = []
            rsvp_codes: dict[str, str] = {}
            for ref, email in zip(invitation["recipient_refs"], emails):
                token = issue_rsvp_token(
                    self.settings.rsvp_signing_secret,
                    event_id=invitation["event_id"],
                    recipient_ref=ref,
                    token_id=new_id("TK"),
                    ttl_seconds=self.settings.rsvp_token_ttl_seconds,
                )
                self.repo.save_token(
                    {
                        "token_id": token.token_id,
                        "event_id": invitation["event_id"],
                        "invitation_id": invitation["invitation_id"],
                        "token_hash": hash_token(token.token),
                        "rsvp_code": token.rsvp_code,
                        "code_hash": hash_code(token.rsvp_code),
                        "recipient_ref": ref,
                        "expires_at": expiry_iso(self.settings.rsvp_token_ttl_seconds),
                        "created_at": self.now_fn(),
                    }
                )
                rsvp_codes[ref] = token.rsvp_code
                body = (
                    invitation["body"]
                    + f"\n\nRSVP link: {self.settings.rsvp_base_url}/rsvp/{token.token}\n"
                    f"Or reply in chat: RSVP {token.rsvp_code} yes\n"
                )
                messages.append(
                    OutgoingEmail(
                        recipient_ref=ref,
                        recipient_email=email,
                        subject=invitation["subject"],
                        body=body,
                        idempotency_key=msg.action_id,
                    )
                )
            report = self.provider.send_batch(messages)
            provider_ids = [o.provider_message_id for o in report.outcomes if o.provider_message_id]
            if report.all_logged_console:
                final_status = InvitationStatus.LOGGED.value
                side_effect = ExternalEffect.NONE.value
            elif report.any_unknown:
                final_status = InvitationStatus.UNKNOWN.value
                side_effect = ExternalEffect.UNCERTAIN.value
            elif report.any_failed:
                final_status = InvitationStatus.FAILED.value
                side_effect = ExternalEffect.NONE.value
            else:
                final_status = InvitationStatus.SENT.value
                side_effect = ExternalEffect.COMPLETED.value
            self.repo.update_invitation(
                invitation["invitation_id"],
                status=final_status,
                provider_message_ids=provider_ids,
                sent_at=self.now_fn() if final_status in (InvitationStatus.SENT.value, InvitationStatus.LOGGED.value) else None,
                failure_reason="; ".join(o.detail for o in report.outcomes if o.detail)[:200] or None,
            )
            self.repo.audit(
                "invitation_send_completed",
                "invitation",
                invitation["invitation_id"],
                final_status,
                event_id=invitation["event_id"],
                metadata={"mode": self.settings.email_provider, "count": len(messages)},
            )
            invitation["status"] = final_status
            invitation["provider_message_ids"] = provider_ids
            response = self._send_response(
                msg, invitation, side_effect, rsvp_codes=rsvp_codes
            )
        except EventPilotError as err:
            self.repo.audit(
                "invitation_send_rejected", "invitation", msg.invitation_id, err.code, event_id=msg.event_id
            )
            response = SendInvitationResponse.failure(
                msg.request_id, err, correlation_id=msg.correlation_id, event_id=msg.event_id
            )
        result.send("coordinator", response)

    def _send_response(
        self,
        msg: SendInvitationRequest,
        invitation: dict,
        side_effect: str,
        rsvp_codes: dict[str, str] | None = None,
    ) -> SendInvitationResponse:
        # RSVP codes are bearer secrets, so they are only echoed when nothing
        # was emailed (console demo mode). With SMTP they travel in the email.
        console_demo = self.settings.email_provider == "console"
        codes = dict(rsvp_codes or {})
        if console_demo and not codes:
            codes = {
                token["recipient_ref"]: token["rsvp_code"]
                for token in self.repo.tokens_for_invitation(invitation["invitation_id"])
            }
        return SendInvitationResponse(
            request_id=msg.request_id,
            correlation_id=msg.correlation_id,
            event_id=msg.event_id,
            plan_version=invitation.get("plan_version"),
            invitation_id=invitation["invitation_id"],
            invitation_status=invitation["status"],
            provider_message_ids=invitation.get("provider_message_ids") or [],
            results=[
                SendResultLine(
                    recipient_ref=ref,
                    status=invitation["status"],
                    rsvp_code=codes.get(ref, "") if console_demo else "",
                )
                for ref in invitation["recipient_refs"]
            ],
            uncertain=invitation["status"] == InvitationStatus.UNKNOWN.value,
            side_effect=side_effect,
        )

    # -- rsvp ---------------------------------------------------------------

    def _on_count_request(self, msg: RsvpCountRequest, result: TurnResult) -> None:
        count = self.repo.confirmed_count(msg.event_id)
        result.send(
            "coordinator",
            RsvpCountResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                confirmed_count=count,
                side_effect=ExternalEffect.NONE.value,
            ),
        )

    def _on_rsvp_submit(self, msg: RsvpSubmitRequest, result: TurnResult) -> None:
        try:
            record = self.repo.get_token_by_code_hash(hash_code(msg.rsvp_code))
            if record is None:
                raise NotFoundError("unknown RSVP code")
            if record["revoked"]:
                raise AuthorizationError("this RSVP code was revoked")
            if record["used_at"] is not None:
                count = self.repo.confirmed_count(record["event_id"])
                response = RsvpSubmitResponse(
                    request_id=msg.request_id,
                    correlation_id=msg.correlation_id,
                    event_id=record["event_id"],
                    confirmed_count=count,
                    already_registered=True,
                )
                result.send("coordinator", response)
                return
            from shared.time_utils import is_expired

            if is_expired(record["expires_at"]):
                raise ExpiredError("this RSVP code has expired")
            decision = (msg.decision or "yes").lower()
            status = RsvpStatus.CONFIRMED.value if decision in ("yes", "y", "confirm") else RsvpStatus.DECLINED.value
            self.repo.record_rsvp(
                event_id=record["event_id"],
                token_id=record["token_id"],
                attendee_reference=msg.attendee_reference or record["recipient_ref"],
                status=status,
            )
            self.repo.mark_token_used(record["token_id"])
            self.repo.audit(
                "rsvp_recorded",
                "rsvp",
                record["token_id"],
                status,
                event_id=record["event_id"],
                metadata={"via": "chat_code"},
            )
            response = RsvpSubmitResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=record["event_id"],
                confirmed_count=self.repo.confirmed_count(record["event_id"]),
                already_registered=False,
            )
        except EventPilotError as err:
            response = RsvpSubmitResponse.failure(
                msg.request_id, err, correlation_id=msg.correlation_id, event_id=msg.event_id
            )
        result.send("coordinator", response)

    # -- web-facing helpers (used by rsvp_web via repository) ------------------

    def fresh_count(self, event_id: str) -> int:
        return self.repo.confirmed_count(event_id)
