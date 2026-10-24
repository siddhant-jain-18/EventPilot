"""ASI1 LLM client (spec section 8).

The LLM sits on the UNTRUSTED side of a validation boundary:

Allowed uses            : interpret a free-text brief, identify missing fields,
                          explain computed trade-offs, draft invitations,
                          summarize an audit timeline.
Prohibited uses         : set prices, override budgets, declare availability,
                          authorize spending or sending, mark payments verified,
                          confirm reservations, or bypass state machines.

Every structured output is schema-validated; every explanation passes a
numeric guard against computed facts (Plan.md 5.2). If ASI1 is unavailable the
callers fall back to deterministic templates and the audit trail records the
fallback. Credentials come from env vars and are never logged.
"""

from __future__ import annotations

import httpx

from shared.errors import IntegrationError, IntegrationUnavailableError
from shared.logging_config import get_logger
from shared.validation import extract_json_object, numeric_guard

logger = get_logger("asi1")


class ASI1Client:
    """Thin OpenAI-compatible client for the ASI1 API.

    Endpoint, model id and auth follow the official ASI:One API quickstart
    (https://docs.asi1.ai). Configure via ASI1_API_KEY / ASI1_BASE_URL /
    ASI1_MODEL. No SDK magic: a plain HTTPS POST to {base}/chat/completions.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.asi1.ai/v1",
        model: str = "asi1-mini",
        timeout_seconds: float = 8.0,
    ):
        if not api_key:
            raise IntegrationUnavailableError("ASI1_API_KEY is not configured")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    # -- low level -------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.2,
        max_tokens: int = 800,
    ) -> str:
        """One completion. Raises IntegrationError on any failure; never logs
        the API key."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as err:
            raise IntegrationError(f"ASI1 request failed: {err}") from err
        if response.status_code == 401:
            raise IntegrationError("ASI1 rejected the credentials (HTTP 401)")
        if response.status_code != 200:
            raise IntegrationError(f"ASI1 returned HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except Exception as err:
            raise IntegrationError(f"unparseable ASI1 response: {err}") from err
        if not isinstance(content, str) or not content.strip():
            raise IntegrationError("ASI1 returned an empty completion")
        return content.strip()

    # -- high level, always validated ------------------------------------------

    def extract_brief_json(self, user_text: str, now_iso: str) -> dict | None:
        """Best-effort structured brief extraction. Returns a raw dict or None;
        the CALLER validates it against BriefDraft and merges it behind
        deterministic parsing."""
        system = (
            "You extract event requirements as strict JSON. Output ONLY a JSON object "
            "with keys: title, event_type, date (YYYY-MM-DD), city, expected_attendees "
            "(integer), budget_inr (integer rupees), equipment_requirements (array of short "
            f"strings). Today is {now_iso}. Omit a key when the text does not state it. "
            "Never invent values."
        )
        try:
            content = self.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_text[:2000]},
                ],
                temperature=0.0,
            )
        except IntegrationError as err:
            logger.warning("ASI1 brief extraction unavailable: %s", err)
            return None
        return extract_json_object(content)

    def explain_options(self, computed_facts: str, allowed_numbers: set[str]) -> str | None:
        """Narrate computed facts. If the response contains any number that is
        not in the facts set, return None (numeric guard tripped) so the caller
        falls back to the deterministic template."""
        try:
            content = self.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "You explain event package options in at most 4 short lines. "
                            "Use ONLY the numbers and amounts given in the facts. Do not "
                            "compute new numbers. Never promise availability or pricing "
                            "beyond the facts. No emojis."
                        ),
                    },
                    {"role": "user", "content": f"FACTS:\n{computed_facts}"},
                ],
                temperature=0.3,
            )
        except IntegrationError as err:
            logger.warning("ASI1 explanation unavailable: %s", err)
            return None
        ok, offending = numeric_guard(content, allowed_numbers)
        if not ok:
            logger.warning("numeric guard tripped; offending tokens: %s", offending)
            return None
        return content

    def draft_invitation(self, event_facts: str, allowed_numbers: set[str]) -> tuple[str, str] | None:
        """Draft (subject, body) from validated facts; guard numbers; the text
        will be hashed and bound to the approval."""
        try:
            content = self.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Draft a short workshop invitation. Output format:\n"
                            "SUBJECT: <subject>\nBODY:\n<body of 4-6 lines>\n"
                            "Use ONLY the facts provided. Do not invent dates, prices, "
                            "names or links. Keep a professional, warm tone. No emojis."
                        ),
                    },
                    {"role": "user", "content": f"FACTS:\n{event_facts}"},
                ],
                temperature=0.4,
                max_tokens=500,
            )
        except IntegrationError as err:
            logger.warning("ASI1 invitation drafting unavailable: %s", err)
            return None
        subject, body = _parse_subject_body(content)
        if not subject or len(body) < 40:
            return None
        ok, _ = numeric_guard(subject + "\n" + body, allowed_numbers)
        if not ok:
            return None
        return subject, body

    def summarize_timeline(self, timeline_text: str) -> str | None:
        try:
            return self.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "Summarize this audit timeline in 3-5 lines for the organizer. "
                            "Use ONLY facts present in the timeline. No emojis."
                        ),
                    },
                    {"role": "user", "content": timeline_text[:4000]},
                ],
                temperature=0.2,
            )
        except IntegrationError as err:
            logger.warning("ASI1 timeline summary unavailable: %s", err)
            return None


def _parse_subject_body(content: str) -> tuple[str, str]:
    subject = ""
    body = content
    lowered = content.lower()
    if "subject:" in lowered:
        idx = lowered.index("subject:")
        end = content.find("\n", idx)
        subject = content[idx + len("subject:") : end if end != -1 else len(content)].strip()
        body_marker = lowered.find("body:", idx)
        if body_marker != -1:
            body = content[body_marker + len("body:") :].lstrip(":\n").strip()
        else:
            body = content[end + 1 :].strip() if end != -1 else ""
    return subject[:200], body
