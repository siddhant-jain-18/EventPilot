"""Money handling, parsers, hashing, allowlists and the LLM numeric guard.

Deterministic code owns every number that matters. Nothing here trusts text
produced by a user, a card, an LLM or another agent: values are parsed,
validated and re-checked server-side.

Money rules (spec section 5 / Plan.md section 4):
- INR is integer paise. Rs 20,000 == 2_000_000 paise. Floats are forbidden.
- tFET deposits are integer atto-FET (1 tFET == 10**18 atestfet).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal

from shared.errors import ValidationError

INR_FREE_TEXT_RE = re.compile(
    r"(under|below|upto|up\s+to|budget(?:\s+of)?|max(?:imum)?|expenses?\s+under)?[\s]*"
    r"(rs\.?|inr|\u20B9)?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(k|lakh|lac|cr|crore)?",
    re.IGNORECASE,
)
ATTENDEES_RE = re.compile(r"(\d[\d,]*)\s*(?:people|persons?|attendees?|guests?|pax|seats?|participants?)", re.IGNORECASE)
BARE_NUMBER_RE = re.compile(r"^\d{1,6}$")

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_NAME_RE = re.compile(
    r"(?:(\d{1,2})(?:st|nd|rd|th)?[\s-]+)?([A-Za-z]{3,9})[\s,-]+(\d{4})",
)
MONTH_FIRST_RE = re.compile(
    r"([A-Za-z]{3,9})\.?[\s-]+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})",
)
DATE_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
NUMERIC_TOKEN_RE = re.compile(r"(?:\u20B9|rs\.?|inr)?\s*\d[\d,]*(?:\.\d+)?\s*(?:tFET|k|lakh|lac)?", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def validate_paise(value: object, *, field: str = "amount") -> int:
    """Coerce/validate an integer paise amount. Floats and strings are rejected."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer number of paise (no floats)", details={"got": repr(value)})
    if value < 0:
        raise ValidationError(f"{field} must be non-negative")
    return value


def format_inr(paise: int) -> str:
    """Format integer paise as Indian-grouped rupees, e.g. 1920000 -> 'Rs 19,200'."""
    paise = validate_paise(paise)
    rupees, rem = divmod(paise, 100)
    rupee_str = _indian_group(rupees)
    return f"\u20B9{rupee_str}.{rem:02d}" if rem else f"\u20B9{rupee_str}"


def _indian_group(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = [tail]
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts)


def format_fet(base_units: int) -> str:
    """Format integer atto-FET as a tFET decimal string without floats."""
    if not isinstance(base_units, int) or isinstance(base_units, bool) or base_units < 0:
        raise ValidationError("FET amount must be a non-negative integer of atto-FET")
    d = (Decimal(base_units) / (10**18)).normalize()
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text} tFET"


def rupees_to_paise(rupees: int) -> int:
    return validate_paise(int(rupees) * 100)


# ---------------------------------------------------------------------------
# Free-text parsers (deterministic first pass; ASI1 fills gaps behind validation)
# ---------------------------------------------------------------------------


def parse_attendees(text: str) -> int | None:
    m = ATTENDEES_RE.search(text)
    if m:
        value = int(m.group(1).replace(",", ""))
        return value if 1 <= value <= 10000 else None
    m2 = re.search(r"\bfor\s+(\d{1,4})\b", text, re.IGNORECASE)
    if m2:
        value = int(m2.group(1))
        return value if 1 <= value <= 10000 else None
    return None


def parse_budget_paise(text: str) -> int | None:
    """Parse the budget from free text.

    Preference order avoids the classic 'for 60 people' trap:
    1. the first number that carries explicit context ('under', 'budget of',
       'max') or an explicit currency marker (Rs/INR/rupee symbol),
    2. otherwise the first bare number that is at least plausible as a budget
       (>= Rs 100).
    """
    with_context: int | None = None
    first_plausible: int | None = None
    for match in INR_FREE_TEXT_RE.finditer(text):
        context, currency, raw, unit = match.groups()
        if not raw:
            continue
        try:
            number = Decimal(raw.replace(",", ""))
        except Exception:
            continue
        unit = (unit or "").lower()
        if unit == "k":
            number *= 1_000
        elif unit in ("lakh", "lac"):
            number *= 100_000
        elif unit in ("cr", "crore"):
            number *= 10_000_000
        rupees = int(number)
        if rupees <= 0 or rupees > 1_000_000_000:
            continue
        if first_plausible is None:
            first_plausible = rupees
        if (context or currency) and with_context is None:
            with_context = rupees
            break
    chosen = with_context
    if chosen is None and first_plausible is not None and first_plausible >= 100:
        chosen = first_plausible
    return chosen * 100 if chosen else None


def parse_event_date(text: str, *, today: date | None = None) -> str | None:
    """Extract an event date; returns an ISO date string or None."""
    m = DATE_ISO_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = MONTH_NAME_RE.search(text)  # day-month-year: 15 November 2026
    if m:
        parsed = _month_day_year(m.group(1), m.group(2), m.group(3))
        if parsed:
            return parsed
    m = MONTH_FIRST_RE.search(text)  # month-day-year: November 15, 2026
    if m:
        parsed = _month_day_year(m.group(2), m.group(1), m.group(3))
        if parsed:
            return parsed
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(_find_date_component(text, fmt), fmt).date().isoformat()
        except Exception:
            continue
    return None


def _month_day_year(day_raw: str | None, month_raw: str, year_raw: str) -> str | None:
    month = MONTHS.get(month_raw.lower())
    if not month:
        return None
    try:
        year = int(year_raw)
    except ValueError:
        return None
    if day_raw:
        try:
            return date(year, month, int(day_raw)).isoformat()
        except ValueError:
            return None
    return None  # month + year only: not concrete enough


def _find_date_component(text: str, fmt: str) -> str:
    m = re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", text)
    if not m:
        raise ValueError("no numeric date")
    return m.group(0)


def normalize_equipment(items: list[str] | str | None) -> list[str]:
    """Lowercase, de-duplicate equipment requirement tokens."""
    if items is None:
        return []
    if isinstance(items, str):
        items = [items]
    seen: list[str] = []
    for item in items:
        token = re.sub(r"[^a-z0-9_ ]", "", str(item).strip().lower().replace("-", "_"))
        token = re.sub(r"\s+", "_", token)
        if token and token not in seen:
            seen.append(token)
    return seen


# ---------------------------------------------------------------------------
# Hashing and identifiers
# ---------------------------------------------------------------------------


def canonical_payload(payload: object) -> str:
    """Deterministic JSON encoding used for every approval snapshot hash."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(payload: object) -> str:
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_allowlisted_email(address: str, allowlist: list[str]) -> bool:
    """Exact, case-insensitive allowlist match. Never a domain-wide match."""
    if not address or not allowlist:
        return False
    return address.strip().lower() in {a.strip().lower() for a in allowlist if a.strip()}


# ---------------------------------------------------------------------------
# Numeric guard for LLM-generated prose (Plan.md section 5.2)
# ---------------------------------------------------------------------------


def numeric_tokens(text: str) -> list[str]:
    """Extract money-ish/number-ish tokens from text, normalised."""
    tokens: list[str] = []
    for raw in NUMERIC_TOKEN_RE.findall(text):
        token = raw.strip().lower()
        token = token.replace("rs.", "₹").replace("rs", "₹").replace("inr", "₹")
        token = token.replace(" ", "").replace(",", "")
        if token.startswith("₹"):
            token = "₹" + token[1:]
        tokens.append(token)
    return tokens


def build_allowed_numbers(*values: object) -> set[str]:
    """Build the allowed token set from known-good numeric facts.

    Accepts ints (paise / base units / counts) and strings. Every integer is
    registered as rupees-from-paise, plain int, and comma-grouped variants so
    legitimate formatting differences never trigger the guard.
    """
    allowed: set[str] = set()
    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            plain = str(value)
            allowed.update({plain, f"₹{plain}", f"₹{_indian_group(value)}"})
            if value >= 100 and value % 100 == 0:
                rupees = value // 100
                allowed.update({str(rupees), f"₹{rupees}", f"₹{_indian_group(rupees)}"})
        elif isinstance(value, float):
            continue  # floats never appear in authoritative facts
        else:
            text = str(value).strip().lower().replace(" ", "")
            if text:
                allowed.add(text)
    return allowed


def numeric_guard(text: str, allowed: set[str]) -> tuple[bool, list[str]]:
    """Return (ok, offending_tokens).

    Every number-like token in ``text`` must appear in ``allowed`` (both sides
    normalised: commas/spaces stripped, lower-cased). If the guard fails the
    caller must fall back to a deterministic template instead of showing the
    LLM's numbers.
    """
    normalized_allowed = {
        token.replace(",", "").replace(" ", "").lower() for token in allowed if token
    }
    offending = [
        token for token in numeric_tokens(text) if token not in normalized_allowed
    ]
    return (not offending, offending)


def extract_json_object(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM response, tolerating fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for idx in range(start, len(text)):
        if text[idx] == "{":
            depth += 1
        elif text[idx] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : idx + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None
