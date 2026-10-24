"""Deterministic planning and budget rules (spec section 9).

The LLM NEVER decides eligibility. A package is eligible only if:
1. capacity >= attendee count,
2. it satisfies every mandatory equipment requirement,
3. its availability permits quoting,
4. its price is within budget (price <= budget; the cap is "must not exceed",
   so the == boundary passes and is covered by a dedicated test),
5. its quote is still valid at selection time.

Budget accounting is a committed/proposed ledger so an amendment REPLACES the
committed package instead of stacking spend (Plan.md section 4 rule 3).
"""

from __future__ import annotations

from typing import Optional

from shared.errors import BudgetExceededError, CapacityExceededError, ValidationError
from shared.inventory import PACKAGE_DEPOSITS_ATFET
from shared.models import EligibilityCheck
from shared.time_utils import is_expired
from shared.validation import normalize_equipment, validate_paise


def evaluate_packages(
    *,
    attendee_count: int,
    budget_inr_paise: int,
    required_equipment: list[str],
    packages: list[dict],
    quotes_by_package: dict[str, dict],
) -> list[EligibilityCheck]:
    """Score every package against the event constraints. Pure function."""
    budget_inr_paise = validate_paise(budget_inr_paise, field="budget")
    required = set(normalize_equipment(required_equipment))
    checks: list[EligibilityCheck] = []
    for pkg in packages:
        reasons: list[str] = []
        if pkg["capacity"] < attendee_count:
            reasons.append("capacity_insufficient")
        price = validate_paise(pkg["price_inr_paise"], field=f"price[{pkg['package_id']}]")
        if price > budget_inr_paise:
            over = price - budget_inr_paise
            reasons.append("over_budget")
        elif price == budget_inr_paise:
            pass  # boundary: budget cap is "must not exceed"; equality is allowed
        missing = required - set(normalize_equipment(pkg.get("equipment") or []))
        if missing:
            reasons.append("equipment_missing")
        if pkg.get("availability_status") != "available":
            reasons.append("unavailable")
        quote = quotes_by_package.get(pkg["package_id"])
        if quote is None:
            # The supplier intentionally does not quote packages whose capacity
            # cannot host the count; only flag missing quotes when capacity is fine.
            if "capacity_insufficient" not in reasons:
                reasons.append("quote_missing")
        elif quote.get("status") != "active" or is_expired(quote["expires_at"]):
            reasons.append("quote_expired")
        eligible = not reasons
        checks.append(
            EligibilityCheck(
                package_id=pkg["package_id"],
                package_name=pkg["name"],
                capacity=pkg["capacity"],
                price_inr_paise=price,
                deposit_fet_base=PACKAGE_DEPOSITS_ATFET.get(pkg["package_id"], 0),
                quote_id=quote["quote_id"] if quote else None,
                eligible=eligible,
                reasons=reasons,
                headroom_inr_paise=(budget_inr_paise - price) if eligible else None,
            )
        )
    checks.sort(key=lambda c: (not c.eligible, c.price_inr_paise))
    return checks


def validate_selection(
    checks: list[EligibilityCheck],
    package_id: str,
    *,
    quote_id: str | None = None,
) -> EligibilityCheck:
    """Validate a selection against freshly computed eligibility."""
    match: Optional[EligibilityCheck] = None
    for check in checks:
        if check.package_id == package_id and (quote_id is None or check.quote_id == quote_id):
            match = check
            break
    if match is None:
        raise ValidationError(
            "selected package is not part of the current plan options",
            details={"package_id": package_id, "quote_id": quote_id},
        )
    if not match.eligible:
        if "capacity_insufficient" in match.reasons:
            raise CapacityExceededError(
                f"{match.package_name} capacity {match.capacity} is below the required attendee count"
            )
        if "over_budget" in match.reasons:
            raise BudgetExceededError(
                f"{match.package_name} price exceeds the event budget",
                details={"reasons": match.reasons},
            )
        raise ValidationError(
            f"{match.package_name} is not eligible: {', '.join(match.reasons)}",
            details={"reasons": match.reasons},
        )
    return match


def plan_narration(checks: list[EligibilityCheck], *, attendee_count: int, budget_inr_paise: int) -> str:
    """Deterministic plain-text explanation of the options (card fallback)."""
    from shared.validation import format_fet, format_inr

    lines = [
        f"Options for {attendee_count} attendees with budget {format_inr(budget_inr_paise)} "
        f"(budget is a hard cap; demo inventory):"
    ]
    for idx, check in enumerate(checks, start=1):
        if check.eligible:
            head = format_inr(check.headroom_inr_paise or 0)
            lines.append(
                f"[{idx}] FITS  {check.package_name} — {format_inr(check.price_inr_paise)} "
                f"(headroom {head}, capacity {check.capacity}, deposit {format_fet(check.deposit_fet_base)})"
            )
        else:
            pretty = ", ".join(reason.replace("_", " ") for reason in check.reasons)
            lines.append(
                f"[{idx}] BLOCKED  {check.package_name} — {format_inr(check.price_inr_paise)} "
                f"(capacity {check.capacity}) — {pretty}"
            )
    lines.append("Approve an exact package with its approval code before anything is spent.")
    return "\n".join(lines)


def committed_plus_proposed_within_budget(
    committed: int, proposed: int, budget: int
) -> bool:
    """Budget rule: committed + proposed must never exceed the budget."""
    return committed + proposed <= budget


def fresh_quote_needed(quote: Optional[dict]) -> bool:
    """True when the stored quote can no longer be trusted and fresh quotes
    must be requested from the supplier."""
    if quote is None:
        return True
    return quote.get("status") != "active" or is_expired(quote["expires_at"])


def quote_expiry_guard(quote: dict) -> None:
    """Raise when a quote can no longer be selected (server-side record only)."""
    if quote.get("status") != "active":
        raise ValidationError(f"quote {quote['quote_id']} is {quote.get('status')}, not active")
    if is_expired(quote["expires_at"]):
        raise ValidationError(f"quote {quote['quote_id']} expired at {quote['expires_at']}")
