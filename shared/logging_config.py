"""Structured logging with correlation IDs and secret redaction.

Rules enforced here (spec section 13):
- logs carry a correlation id when one is set;
- private keys, API keys, authorization headers, attendee e-mail addresses,
  signed RSVP tokens and seeds are redacted before a record is emitted.
"""

from __future__ import annotations

import contextvars
import logging
import re
import sys
from datetime import datetime, timezone

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)

_REDACTED = "***REDACTED***"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_SECRET_KEY_RE = re.compile(
    r"(api_key|apikey|authorization|seed|secret|password|private_key|token)"
    r"[\s]*[:=][\s]*\S+",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def set_correlation_id(value: str) -> contextvars.Token:
    return _correlation_id.set(value or "")


def get_correlation_id() -> str:
    return _correlation_id.get()


def redact(text: str) -> str:
    """Best-effort scrubbing of secrets and personal data from a log string."""
    text = _SECRET_KEY_RE.sub(lambda m: m.group(1) + "=" + _REDACTED, text)
    text = _EMAIL_RE.sub(_REDACTED, text)
    text = _TOKEN_RE.sub(_REDACTED, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        except Exception:  # pragma: no cover - never break logging
            pass
        return True


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation = get_correlation_id()
        if correlation:
            payload["correlation_id"] = correlation
        extra = getattr(record, "safe_extra", None)
        if extra:
            payload["extra"] = extra
        return redact(str(payload))


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    # third-party noise
    for noisy in ("urllib3", "httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
