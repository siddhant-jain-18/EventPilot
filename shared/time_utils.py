"""UTC-first time helpers with local-timezone display."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:  # Python 3.9+ zoneinfo; tzdata present on standard Linux images
    from zoneinfo import ZoneInfo

    _HAS_ZONEINFO = True
except Exception:  # pragma: no cover
    _HAS_ZONEINFO = False

DEFAULT_TZ = "Asia/Kolkata"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Canonical ISO-8601 UTC string (second precision, +00:00 suffix)."""
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(utc_now())


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def expiry_iso(seconds: int, *, now: datetime | None = None) -> str:
    base = now or utc_now()
    return iso(base + timedelta(seconds=seconds))


def is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    return (now or utc_now()) >= parse_iso(expires_at)


def format_local(iso_ts: str, tz_name: str = DEFAULT_TZ) -> str:
    """Display a UTC timestamp in the event's local timezone."""
    dt = parse_iso(iso_ts)
    if _HAS_ZONEINFO:
        try:
            return dt.astimezone(ZoneInfo(tz_name)).strftime("%d %b %Y, %H:%M %Z")
        except Exception:  # pragma: no cover - unknown tz database entry
            pass
    return dt.strftime("%d %b %Y, %H:%M UTC")


def elapsed_ms(start: datetime, end: datetime | None = None) -> int:
    return max(0, int(((end or utc_now()) - start).total_seconds() * 1000))
