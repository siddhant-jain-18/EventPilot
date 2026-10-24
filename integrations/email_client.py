"""Email provider abstraction (spec section 12).

Two providers:

- ``ConsoleEmailProvider`` (default): writes a masked delivery record to the
  logs. The result status is ``logged_console`` — an explicit development-mode
  outcome that is NEVER reported as a real send.
- ``SmtpEmailProvider``: real delivery through SMTP with a timeout. Recipient
  refusals map to ``failed``; timeouts/transport errors map to ``unknown``
  because the provider cannot prove whether delivery happened — the caller
  must reconcile instead of blindly resending.

Safety rules enforced by the caller (OutreachService):
- recipients come only from ALLOWED_TEST_RECIPIENTS (allowlist, never
  user-supplied during a session),
- one idempotency key per approved send (duplicate sends are impossible),
- attendee e-mail addresses never appear in chat, URLs, agent messages or
  audit metadata — only inside this provider boundary.
"""

from __future__ import annotations

import re
import smtplib
import ssl
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from uuid import uuid4

from shared.logging_config import get_logger
from shared.time_utils import now_iso

logger = get_logger("email")

STATUS_SENT = "sent"
STATUS_LOGGED_CONSOLE = "logged_console"
STATUS_FAILED = "failed"
STATUS_UNKNOWN = "unknown"

_EMAIL_RE = re.compile(r"^(.*)@(.*)$")


def mask_email(address: str) -> str:
    """jane.doe@example.com -> j***@e******.com (for logs and chat)."""
    match = _EMAIL_RE.match(address or "")
    if not match:
        return "***"
    local, domain = match.groups()
    domain_parts = domain.split(".", 1)
    masked_domain = (
        domain_parts[0][0] + "*" * max(0, len(domain_parts[0]) - 1)
        + ("." + domain_parts[1] if len(domain_parts) > 1 else "")
    )
    return f"{(local or '*')[0]}***@{masked_domain}"


@dataclass
class OutgoingEmail:
    recipient_ref: str
    recipient_email: str
    subject: str
    body: str
    idempotency_key: str


@dataclass
class SendOutcome:
    recipient_ref: str
    status: str
    provider_message_id: str = ""
    detail: str = ""


@dataclass
class SendReport:
    outcomes: list[SendOutcome] = field(default_factory=list)

    @property
    def all_logged_console(self) -> bool:
        return bool(self.outcomes) and all(o.status == STATUS_LOGGED_CONSOLE for o in self.outcomes)

    @property
    def any_failed(self) -> bool:
        return any(o.status == STATUS_FAILED for o in self.outcomes)

    @property
    def any_unknown(self) -> bool:
        return any(o.status == STATUS_UNKNOWN for o in self.outcomes)


class EmailProvider:
    def send_batch(self, messages: list[OutgoingEmail]) -> SendReport:  # pragma: no cover
        raise NotImplementedError


class ConsoleEmailProvider:
    """Development mode: log a masked record, return an explicit
    'logged_console' status. Never pretends an e-mail was delivered."""

    def send_batch(self, messages: list[OutgoingEmail]) -> SendReport:
        report = SendReport()
        for message in messages:
            message_id = f"console-{uuid4().hex[:12]}"
            logger.info(
                "EMAIL[console mode, NOT delivered] id=%s to=%s ref=%s subject=%r "
                "idempotency_key=%s body_lines=%d ts=%s",
                message_id,
                mask_email(message.recipient_email),
                message.recipient_ref,
                message.subject,
                message.idempotency_key,
                message.body.count("\n") + 1,
                now_iso(),
            )
            report.outcomes.append(
                SendOutcome(
                    recipient_ref=message.recipient_ref,
                    status=STATUS_LOGGED_CONSOLE,
                    provider_message_id=message_id,
                )
            )
        return report


class SmtpEmailProvider:
    """Real delivery via SMTP (STARTTLS). Timeouts map to 'unknown'."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_address: str,
        timeout_seconds: float = 15.0,
    ):
        if not host or not from_address:
            raise ValueError("SMTP host and from address are required")
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.timeout_seconds = timeout_seconds

    def send_batch(self, messages: list[OutgoingEmail]) -> SendReport:
        report = SendReport()
        for message in messages:
            report.outcomes.append(self._send_one(message))
        return report

    def _send_one(self, message: OutgoingEmail) -> SendOutcome:
        mime = MIMEMultipart("alternative")
        mime["Subject"] = message.subject
        mime["From"] = self.from_address
        mime["To"] = message.recipient_email
        mime["X-Idempotency-Key"] = message.idempotency_key
        mime.attach(MIMEText(message.body, "plain", "utf-8"))
        context = ssl.create_default_context()
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as server:
                server.starttls(context=context)
                if self.username:
                    server.login(self.username, self.password)
                refusal = server.send_message(mime)
            if refusal:  # {recipient: (code, msg)} for refused recipients
                first = next(iter(refusal.values()), ("", ""))
                return SendOutcome(
                    recipient_ref=message.recipient_ref,
                    status=STATUS_FAILED,
                    detail=f"refused: {first[1] if isinstance(first, tuple) else first}",
                )
            return SendOutcome(
                recipient_ref=message.recipient_ref,
                status=STATUS_SENT,
                provider_message_id=f"smtp-{uuid4().hex[:12]}",
            )
        except smtplib.SMTPRecipientsRefused as err:
            return SendOutcome(
                recipient_ref=message.recipient_ref,
                status=STATUS_FAILED,
                detail=f"recipients refused: {err}",
            )
        except (smtplib.SMTPException, TimeoutError, OSError) as err:
            # The provider cannot prove delivery or non-delivery: unknown.
            return SendOutcome(
                recipient_ref=message.recipient_ref,
                status=STATUS_UNKNOWN,
                detail=f"uncertain outcome: {err}",
            )


def build_provider(settings) -> EmailProvider:
    if settings.email_provider == "smtp":
        return SmtpEmailProvider(
            host=settings.email_smtp_host,
            port=settings.email_smtp_port,
            username=settings.email_smtp_user,
            password=settings.email_smtp_password,
            from_address=settings.email_from,
        )
    return ConsoleEmailProvider()
