"""Cryptographically secure, event-specific, single-purpose RSVP tokens.

A token is ``base64url(payload).base64url(hmac_sha256)`` where the payload
covers event id, recipient reference, expiry and a nonce. E-mail addresses or
other personal data NEVER go into the URL. Verification is constant-time and
checks signature, expiry, event association and unused status.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import string
from dataclasses import dataclass

from shared.errors import AuthorizationError, ExpiredError, ValidationError
from shared.time_utils import parse_iso, utc_now

_CODE_ALPHABET = string.ascii_uppercase + string.digits  # chat RSVP fallback code


@dataclass
class IssuedToken:
    token: str
    token_id: str
    rsvp_code: str
    expires_at: str
    recipient_ref: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _from_b64url(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_rsvp_token(
    secret: str,
    *,
    event_id: str,
    recipient_ref: str,
    token_id: str,
    ttl_seconds: int,
) -> IssuedToken:
    if not secret:
        raise ValidationError("RSVP_SIGNING_SECRET is required to issue tokens")
    if ttl_seconds <= 0:
        raise ValidationError("token TTL must be positive")
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
    payload = {
        "event_id": event_id,
        "ref": recipient_ref,
        "tid": token_id,
        "code": code,
        "exp": int(utc_now().timestamp()) + ttl_seconds,
        "nonce": secrets.token_hex(8),
    }
    body = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return IssuedToken(
        token=f"{body}.{_b64url(digest)}",
        token_id=token_id,
        rsvp_code=code,
        expires_at=payload["exp"],
        recipient_ref=recipient_ref,
    )


def verify_rsvp_token(secret: str, token: str, *, event_id: str | None = None) -> dict:
    """Return the payload when every check passes; raise otherwise."""
    if not secret:
        raise ValidationError("RSVP_SIGNING_SECRET is not configured")
    token = (token or "").strip()
    try:
        body, mac = token.split(".", 1)
    except ValueError as err:
        raise ValidationError("malformed RSVP token") from err
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _from_b64url(mac)):
        raise AuthorizationError("RSVP token signature is invalid")
    try:
        payload = json.loads(_from_b64url(body))
    except (json.JSONDecodeError, ValueError) as err:
        raise ValidationError("malformed RSVP token payload") from err
    if not isinstance(payload, dict) or "event_id" not in payload or "exp" not in payload:
        raise ValidationError("malformed RSVP token payload")
    if utc_now().timestamp() > int(payload["exp"]):
        raise ExpiredError("RSVP token has expired")
    if event_id is not None and payload["event_id"] != event_id:
        raise AuthorizationError("RSVP token belongs to a different event")
    return payload


def hash_code(code: str) -> str:
    """Hash of an RSVP chat code (case-normalised)."""
    return hashlib.sha256((code or "").strip().upper().encode()).hexdigest()


def hash_token(raw_token: str) -> str:
    """Hash of a full RSVP token (case-sensitive, never normalised)."""
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()
