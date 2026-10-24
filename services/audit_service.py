"""Audit service: append-only, correlation-tagged trail of every meaningful
action with sanitized metadata (spec sections 5/13). Powers the `timeline`
command and the final demo evidence.
"""

from __future__ import annotations

import json

from shared.enums import Actor, EntityType
from shared.logging_config import get_correlation_id, redact
from shared.time_utils import now_iso
from storage.database import new_id


class AuditService:
    def __init__(self, repo):
        self.repo = repo  # any repository exposing .conn with an audit_log table

    def record(
        self,
        *,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str = "",
        result: str = "ok",
        event_id: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        safe_metadata = _sanitize(metadata or {})
        audit_id = new_id("AU")
        self.repo.conn.execute(
            "INSERT INTO audit_log (audit_id, event_id, correlation_id, actor, action,"
            " entity_type, entity_id, result, timestamp, safe_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id,
                event_id,
                get_correlation_id(),
                actor,
                action,
                entity_type,
                entity_id,
                result,
                now_iso(),
                json.dumps(safe_metadata, separators=(",", ":")),
            ),
        )
        self.repo.conn.commit()
        return audit_id

    def timeline(self, event_id: str) -> list[dict]:
        rows = self.repo.conn.execute(
            "SELECT * FROM audit_log WHERE event_id = ? ORDER BY rowid",
            (event_id,),
        ).fetchall()
        out = []
        for row in rows:
            data = dict(row)
            try:
                data["safe_metadata"] = json.loads(data.get("safe_metadata") or "{}")
            except json.JSONDecodeError:
                data["safe_metadata"] = {}
            out.append(data)
        return out

    def timeline_narration(self, event_id: str) -> str:
        entries = self.timeline(event_id)
        if not entries:
            return "No audit entries yet for this event."
        lines = [f"Audit timeline for {event_id} ({len(entries)} entries, oldest first):"]
        for entry in entries:
            meta = entry.get("safe_metadata") or {}
            extra = ""
            if meta:
                items = ", ".join(f"{k}={v}" for k, v in sorted(meta.items()))
                extra = f" ({items})"
            lines.append(
                f"- {entry['timestamp']} [{entry['actor']}] {entry['action']} "
                f"{entry['entity_type']}:{entry['entity_id']} -> {entry['result']}{extra}"
            )
        return "\n".join(lines)


def _sanitize(metadata: dict) -> dict:
    """Drop keys that could carry secrets or personal data; redact strings."""
    forbidden = {
        "email", "emails", "recipient_emails", "body", "token", "rsvp_token",
        "api_key", "seed", "private_key", "authorization", "password",
        "transaction_raw", "recipient_address_full",
    }
    safe: dict = {}
    for key, value in metadata.items():
        if key.lower() in forbidden:
            continue
        if isinstance(value, str):
            safe[key] = redact(value)
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
        else:
            safe[key] = redact(str(value))[:200]
    return safe
