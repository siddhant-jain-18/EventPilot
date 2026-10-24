"""Quote creation (supplier side) and quote-response validation (coordinator side).

The supplier computes prices deterministically from its own inventory and
issues expiring quote ids. The coordinator never trusts prices that arrive in
messages, cards or LLM text: every incoming quote line is re-validated against
the immutable demo catalog before it may influence a plan.
"""

from __future__ import annotations

from typing import Optional

from shared.errors import ValidationError
from shared.inventory import DEMO_CATALOG, PACKAGE_DEPOSITS_ATFET, SUPPLIER_ID
from shared.time_utils import expiry_iso, now_iso
from shared.validation import validate_paise
from storage.database import new_id


# ---------------------------------------------------------------------------
# Supplier side
# ---------------------------------------------------------------------------


class SupplierQuoteService:
    def __init__(self, repo, ttl_seconds: int = 1800, ttl_for_test: int | None = None):
        self.repo = repo
        self.ttl_seconds = ttl_for_test if ttl_for_test is not None else ttl_seconds

    def create_quotes(
        self,
        *,
        event_id: str,
        plan_version: int,
        attendee_count: int,
        equipment_requirements: list[str],
        amendment_of_reservation_id: str | None = None,
    ) -> list[dict]:
        """Issue quotes for every available package that can host the count."""
        if attendee_count < 1 or attendee_count > 10000:
            raise ValidationError(f"invalid attendee count: {attendee_count}")
        created: list[dict] = []
        ts = now_iso()
        for pkg in self.repo.list_packages():
            if pkg["availability_status"] != "available":
                continue
            if pkg["capacity"] < attendee_count:
                continue
            deposit = PACKAGE_DEPOSITS_ATFET.get(pkg["package_id"], 0)
            if amendment_of_reservation_id:
                # Amendment quotes carry the DEPOSIT DELTA relative to the
                # reservation being amended (Plan.md section 6.3). They are
                # only issued for reservations that were actually paid.
                prior = self.repo.get_reservation(amendment_of_reservation_id)
                if prior.get("status") not in ("paid", "confirmed"):
                    raise ValidationError(
                        "amendment quotes require the prior reservation to be paid"
                    )
                old_deposit = self._reservation_deposit(amendment_of_reservation_id)
                deposit = max(0, deposit - old_deposit)
            quote = {
                "quote_id": new_id("Q"),
                "event_id": event_id,
                "plan_version": plan_version,
                "package_id": pkg["package_id"],
                "attendee_count": attendee_count,
                "price_inr_paise": pkg["price_inr_paise"],
                "currency": "INR",
                "deposit_fet_base": deposit,
                "inclusions": pkg.get("inclusions") or [],
                "status": "active",
                "created_at": ts,
                "expires_at": expiry_iso(self.ttl_seconds),
                "amendment_of_reservation_id": amendment_of_reservation_id,
            }
            self.repo.save_quote(quote)
            created.append(quote)
        return created

    def _reservation_deposit(self, reservation_id: str) -> int:
        reservation = self.repo.get_reservation(reservation_id)
        quote = self.repo.get_quote(reservation["quote_id"])
        base_deposit = PACKAGE_DEPOSITS_ATFET.get(quote["package_id"], 0)
        return base_deposit


# ---------------------------------------------------------------------------
# Coordinator side
# ---------------------------------------------------------------------------


class CoordinatorQuoteService:
    """Validate supplier responses before they may influence a plan."""

    def __init__(self, repo):
        self.repo = repo

    def validate_response(
        self,
        response: dict,
        *,
        event: dict,
        expected_request_id: str,
        attendee_count: int,
        plan_version: int,
    ) -> list[dict]:
        """Return normalized quote dicts; raises ValidationError on any mismatch.

        Checks (spec sections 6/9 and Plan.md 5.3):
        - response references the pending request (correlation);
        - event id and plan version match the local event state;
        - every line references a package in the trusted demo catalog with the
          catalog's capacity and price (a supplier cannot invent prices);
        - amounts are strict integers; expiry is in the future.
        """
        if response.get("request_id") != expected_request_id:
            raise ValidationError("quote response does not match the pending request")
        if response.get("event_id") != event["event_id"]:
            raise ValidationError("quote response event mismatch")
        if response.get("plan_version") != plan_version:
            raise ValidationError(
                f"quote response plan version {response.get('plan_version')} != {plan_version} (stale)"
            )
        normalized: list[dict] = []
        for line in response.get("quotes") or []:
            package_id = line.get("package_id")
            catalog = DEMO_CATALOG.get(package_id)
            if catalog is None:
                raise ValidationError(f"unknown package in supplier response: {package_id}")
            price = validate_paise(
                line.get("price_inr_paise"), field=f"price[{package_id}]"
            )
            if price != catalog.price_inr_paise:
                raise ValidationError(
                    f"supplier price for {package_id} does not match the trusted catalog"
                )
            if int(line.get("capacity") or 0) != catalog.capacity:
                raise ValidationError(
                    f"supplier capacity for {package_id} does not match the trusted catalog"
                )
            if int(line.get("attendee_count") or 0) != attendee_count:
                raise ValidationError("supplier quote attendee count mismatch")
            deposit = validate_paise(
                line.get("deposit_fet_base"), field=f"deposit[{package_id}]"
            )
            expected_deposit = PACKAGE_DEPOSITS_ATFET.get(package_id, 0)
            amendment_of = line.get("amendment_of_reservation_id")
            if amendment_of:
                # delta deposits are validated at reservation time by the supplier
                if deposit > expected_deposit:
                    raise ValidationError(
                        f"amendment deposit for {package_id} exceeds the package deposit"
                    )
            elif deposit != expected_deposit:
                raise ValidationError(
                    f"deposit for {package_id} does not match the trusted catalog"
                )
            if not line.get("quote_id") or not line.get("expires_at"):
                raise ValidationError("quote line missing quote_id or expiry")
            normalized.append(
                {
                    "quote_id": line["quote_id"],
                    "event_id": event["event_id"],
                    "plan_version": plan_version,
                    "supplier_id": response.get("supplier_id") or SUPPLIER_ID,
                    "package_id": package_id,
                    "package_name": line.get("package_name") or catalog.name,
                    "capacity": catalog.capacity,
                    "attendee_count": attendee_count,
                    "price_inr_paise": price,
                    "currency": "INR",
                    "deposit_fet_base": deposit,
                    "inclusions": line.get("inclusions") or [],
                    "status": "active",
                    "created_at": now_iso(),
                    "expires_at": line["expires_at"],
                    "amendment_of_reservation_id": amendment_of,
                }
            )
        return normalized

    def persist_quotes(self, normalized: list[dict]) -> None:
        for quote in normalized:
            self.repo.save_quote(quote)
