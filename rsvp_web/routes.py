"""RSVP HTTP routes (spec section 12).

- GET  /health                     liveness for monitoring and judges.
- GET  /rsvp/{token}               attendee registration page.
- POST /rsvp/{token}               record an RSVP (duplicate-safe).
- GET  /internal/events/{id}/count agent-protected count query (HMAC header).

Basic per-IP rate limiting protects the public endpoints. The pages and API
never expose administrative data; the count route requires a signed header
computed with RSVP_SIGNING_SECRET over ``{timestamp}:{event_id}``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request

from rsvp_web.tokens import hash_code, hash_token, verify_rsvp_token
from shared.errors import (
    AuthorizationError,
    DuplicateOperationError,
    ExpiredError,
    RateLimitError,
    ValidationError,
)
from shared.logging_config import get_logger

logger = get_logger("rsvp_web")

PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WorkshopPilot RSVP</title>
<style>
 body {{ font-family: system-ui, sans-serif; max-width: 34rem; margin: 3rem auto; padding: 0 1rem; color: #1a1a2e; }}
 .card {{ border: 1px solid #d8d8e8; border-radius: 12px; padding: 1.5rem; }}
 .btn {{ background: #3d8bd3; color: white; border: 0; padding: .6rem 1.2rem; border-radius: 8px; cursor: pointer; }}
 .muted {{ color: #666; font-size: .85rem; }}
 .ok {{ color: #157347; }} .err {{ color: #a12622; }}
 input {{ width: 100%; padding: .4rem; margin: .5rem 0; }}
</style></head>
<body>
<div class="card">
 <h1>You're invited</h1>
 <p>WorkshopPilot demo event <strong>{event_id}</strong>.</p>
 <p class="muted">Inventory, prices and reservations in this demo are fictional demo data.</p>
 {content}
</div>
</body></html>"""

CONFIRM_FORM = """
 <form method="post">
  <input type="hidden" name="decision" value="yes">
  <label for="ref">Your name or reference (optional)</label><br>
  <input id="ref" name="attendee_reference" maxlength="80">
  <button class="btn" type="submit">Confirm my attendance</button>
 </form>"""


class RateLimiter:
    """Naive per-IP sliding window; adequate for a demo endpoint."""

    def __init__(self, max_per_minute: int = 30):
        self.max = max_per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.max:
            raise RateLimitError("too many requests; slow down")
        window.append(now)


def build_router(repos, settings) -> APIRouter:
    router = APIRouter()
    outreach = repos["outreach"]
    limiter = RateLimiter(max_per_minute=settings.rate_limit_per_minute)

    @router.get("/health")
    async def health():
        return {"status": "ok", "service": "workshoppilot-rsvp"}

    def _validate_token_or_404(token: str) -> dict:
        try:
            payload = verify_rsvp_token(settings.rsvp_signing_secret, token)
        except ExpiredError:
            raise HTTPException(status_code=410, detail="This invitation link has expired.")
        except AuthorizationError:
            raise HTTPException(status_code=403, detail="This invitation link is not valid.")
        except ValidationError:
            raise HTTPException(status_code=404, detail="Unknown invitation link.")
        record = outreach.get_token_by_hash(hash_token(token))
        if record is None:
            raise HTTPException(status_code=404, detail="Unknown invitation link.")
        if record["revoked"]:
            raise HTTPException(status_code=410, detail="This invitation was revoked.")
        return record

    @router.get("/rsvp/{token}")
    async def rsvp_page(token: str, request: Request):
        _guard(request)
        record = _validate_token_or_404(token)
        if record["used_at"] is not None:
            content = '<p class="ok">This invitation has already been used to register. Thank you!</p>'
        else:
            content = CONFIRM_FORM
        return PAGE_TEMPLATE.format(event_id=record["event_id"], content=content)

    @router.post("/rsvp/{token}")
    async def rsvp_submit(token: str, request: Request):
        _guard(request)
        record = _validate_token_or_404(token)
        raw = (await request.body()).decode("utf-8", errors="replace")
        form = {k: v[0] for k, v in parse_qs(raw).items() if v}
        if record["used_at"] is not None:
            return PAGE_TEMPLATE.format(
                event_id=record["event_id"],
                content='<p class="err">This invitation was already used to register.</p>',
            )
        try:
            outreach.record_rsvp(
                event_id=record["event_id"],
                token_id=record["token_id"],
                attendee_reference=form.get("attendee_reference") or record["recipient_ref"],
                status="confirmed" if form.get("decision", "yes") == "yes" else "declined",
            )
        except DuplicateOperationError:
            return PAGE_TEMPLATE.format(
                event_id=record["event_id"],
                content='<p class="err">This invitation was already used to register.</p>',
            )
        outreach.mark_token_used(record["token_id"])
        outreach.audit(
            action="rsvp_recorded",
            entity_type="rsvp",
            entity_id=record["token_id"],
            result="ok",
            event_id=record["event_id"],
            metadata={"via": "web"},
        )
        return PAGE_TEMPLATE.format(
            event_id=record["event_id"],
            content='<p class="ok">Registration confirmed. See you there!</p>',
        )

    @router.get("/internal/events/{event_id}/count")
    async def count(event_id: str, request: Request):
        signature = request.headers.get("x-wp-signature", "")
        timestamp = request.headers.get("x-wp-timestamp", "")
        _verify_agent_signature(event_id, timestamp, signature)
        return {"event_id": event_id, "confirmed_count": outreach.confirmed_count(event_id)}

    def _guard(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        try:
            limiter.check(client_ip)
        except RateLimitError as err:
            raise HTTPException(status_code=429, detail=str(err))

    def _verify_agent_signature(event_id: str, timestamp: str, signature: str) -> None:
        if not settings.rsvp_signing_secret:
            raise HTTPException(status_code=503, detail="count endpoint not configured")
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="bad timestamp")
        if abs(time.time() - ts) > 300:
            raise HTTPException(status_code=401, detail="stale timestamp")
        expected = hmac.new(
            settings.rsvp_signing_secret.encode(),
            f"{timestamp}:{event_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature or ""):
            raise HTTPException(status_code=401, detail="invalid signature")

    return router
