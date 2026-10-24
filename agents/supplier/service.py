"""Supplier service (spec section 4.2) — seller and inventory authority.

Owns the demo inventory, quote records (with TTL), reservations and payment
verification. It NEVER trusts a coordinator's claim that a payment succeeded:
evidence is verified independently (payments/verification.py) and transaction
ids are single-use.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from shared.enums import (
    ExternalEffect,
    PaymentMode,
    PaymentStatus,
    QuoteStatus,
    ReservationStatus,
    ResponseStatus,
)
from shared.errors import (
    AuthorizationError,
    DuplicateOperationError,
    EventPilotError,
    ExpiredError,
    NotFoundError,
    ValidationError,
)
from shared.inventory import PACKAGE_DEPOSITS_ATFET
from shared.logging_config import get_logger
from shared.messages import (
    PaymentRequirement,
    PaymentStatusRequest,
    PaymentStatusResponse,
    QuoteLine,
    QuoteRequest,
    QuoteResponse,
    ReservationRequest,
    ReservationResponse,
    ReservationSupersedeRequest,
    ReservationSupersedeResponse,
)
from shared.runtime import Outbound, TurnResult
from shared.time_utils import is_expired, now_iso
from shared.validation import validate_paise
from storage.database import new_id

logger = get_logger("supplier")

ACTIVE_RESERVATION_STATUSES = {"reserved", "payment_pending", "paid", "confirmed"}


class SupplierService:
    def __init__(
        self,
        repo,
        quote_service,
        verifier,
        *,
        supplier_wallet_address: str,
        payment_network: str = "simulated",
        audit=None,
    ):
        self.repo = repo
        self.quotes = quote_service
        self.verifier = verifier
        self.supplier_wallet_address = supplier_wallet_address
        self.payment_network = payment_network
        self.audit = audit

    # ------------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------------

    def handle(self, msg: Any) -> TurnResult:
        result = TurnResult()
        if isinstance(msg, QuoteRequest):
            self._on_quote_request(msg, result)
        elif isinstance(msg, ReservationRequest):
            self._on_reservation_request(msg, result)
        elif isinstance(msg, ReservationSupersedeRequest):
            self._on_supersede_request(msg, result)
        elif isinstance(msg, PaymentStatusRequest):
            self._on_payment_status_request(msg, result)
        else:  # unknown contract — refuse honestly, never loop
            logger.warning("supplier received unsupported message type %s", type(msg).__name__)
        return result

    # ------------------------------------------------------------------

    def _on_quote_request(self, msg: QuoteRequest, result: TurnResult) -> None:
        try:
            self.repo.expire_quotes(now_iso())
            created = self.quotes.create_quotes(
                event_id=msg.event_id,
                plan_version=msg.plan_version or 1,
                attendee_count=msg.attendee_count,
                equipment_requirements=msg.equipment_requirements,
                amendment_of_reservation_id=msg.amendment_of_reservation_id,
            )
            lines = [
                QuoteLine(
                    quote_id=q["quote_id"],
                    package_id=q["package_id"],
                    package_name=self.repo.get_package(q["package_id"])["name"],
                    capacity=self.repo.get_package(q["package_id"])["capacity"],
                    attendee_count=q["attendee_count"],
                    price_inr_paise=q["price_inr_paise"],
                    deposit_fet_base=q["deposit_fet_base"],
                    inclusions=q["inclusions"],
                    created_at=q["created_at"],
                    expires_at=q["expires_at"],
                    amendment_of_reservation_id=q["amendment_of_reservation_id"],
                )
                for q in created
            ]
            response = QuoteResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                plan_version=msg.plan_version,
                supplier_id="EventPilot-supplier-demo",
                quotes=lines,
            )
            self.repo.audit(
                "quote_issued",
                "quote",
                ",".join(q.quote_id for q in lines) or "none",
                "ok",
                event_id=msg.event_id,
                metadata={"count": len(lines), "plan_version": msg.plan_version or 1},
            )
        except EventPilotError as err:
            response = QuoteResponse.failure(
                msg.request_id,
                err,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                plan_version=msg.plan_version,
            )
            self.repo.audit(
                "quote_request_rejected", "quote", msg.request_id, err.code, event_id=msg.event_id
            )
        result.send("coordinator", response)

    def _on_reservation_request(self, msg: ReservationRequest, result: TurnResult) -> None:
        try:
            existing = self.repo.get_reservation_by_action(msg.action_id)
            if existing is not None:
                response = self._reservation_response(
                    msg, existing, replayed=True
                )
                result.send("coordinator", response)
                return

            quote = self.repo.get_quote(msg.quote_id)  # raises NotFoundError
            if quote["event_id"] != msg.event_id:
                raise ValidationError("quote does not belong to the requesting event")
            if quote["package_id"] != msg.package_id:
                raise ValidationError("reservation package does not match the quote")
            if quote["attendee_count"] != msg.attendee_count:
                raise ValidationError("reservation attendee count does not match the quote")
            if quote["status"] != QuoteStatus.ACTIVE.value:
                raise ValidationError(f"quote is {quote['status']}, not active")
            if is_expired(quote["expires_at"]):
                self.repo.set_quote_status(quote["quote_id"], QuoteStatus.EXPIRED.value)
                raise ExpiredError("quote has expired — request fresh quotes")

            if msg.amendment_of_reservation_id:
                prior = self.repo.get_reservation(msg.amendment_of_reservation_id)
                if prior["status"] not in ("paid", "confirmed"):
                    raise ValidationError(
                        "amendment requires the prior reservation to be paid and verified"
                    )
                if prior["event_id"] != msg.event_id:
                    raise AuthorizationError("amendment reservation belongs to another event")
                self.repo.update_reservation(
                    prior["reservation_id"], status=ReservationStatus.SUPERSEDED.value
                )

            ts = now_iso()
            reservation = {
                "reservation_id": new_id("R"),
                "event_id": msg.event_id,
                "plan_version": msg.plan_version or quote["plan_version"],
                "quote_id": quote["quote_id"],
                "package_id": quote["package_id"],
                "status": ReservationStatus.RESERVED.value,
                "payment_mode": PaymentMode.SIMULATED.value,
                "action_id": msg.action_id,
                "created_at": ts,
                "updated_at": ts,
            }
            self.repo.save_reservation(reservation)
            self.repo.set_quote_status(quote["quote_id"], QuoteStatus.ACCEPTED.value)
            self.repo.audit(
                "reservation_created",
                "reservation",
                reservation["reservation_id"],
                "ok",
                event_id=msg.event_id,
                metadata={"package_id": quote["package_id"], "demo_inventory": True},
            )
            response = self._reservation_response(msg, reservation, replayed=False)
        except EventPilotError as err:
            self.repo.audit(
                "reservation_rejected", "reservation", msg.action_id, err.code, event_id=msg.event_id
            )
            response = ReservationResponse.failure(
                msg.request_id,
                err,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                plan_version=msg.plan_version,
            )
        result.send("coordinator", response)

    def _reservation_response(
        self, msg: ReservationRequest, reservation: dict, *, replayed: bool
    ) -> ReservationResponse:
        quote = self.repo.get_quote(reservation["quote_id"])
        requirement = PaymentRequirement(
            quote_id=quote["quote_id"],
            reservation_id=reservation["reservation_id"],
            expected_amount_fet_base=quote["deposit_fet_base"],
            asset="atestfet",
            network=self.payment_network,
            recipient_address=self.supplier_wallet_address,
            expires_at=quote["expires_at"],
        )
        return ReservationResponse(
            request_id=msg.request_id,
            correlation_id=msg.correlation_id,
            event_id=msg.event_id,
            plan_version=reservation["plan_version"],
            reservation_id=reservation["reservation_id"],
            reservation_status=reservation["status"],
            payment_requirement=requirement,
            side_effect=ExternalEffect.COMPLETED.value,  # demo inventory reservation
            replayed=replayed,
        )

    def _on_supersede_request(self, msg: ReservationSupersedeRequest, result: TurnResult) -> None:
        try:
            existing = self.repo.get_reservation_by_action(msg.action_id)
            if existing is not None:
                response = ReservationSupersedeResponse(
                    request_id=msg.request_id,
                    correlation_id=msg.correlation_id,
                    event_id=existing["event_id"],
                    reservation_id=existing["reservation_id"],
                    reservation_status=existing["status"],
                    side_effect=ExternalEffect.NONE.value,
                )
                result.send("coordinator", response)
                return
            reservation = self.repo.get_reservation(msg.reservation_id)
            if reservation["status"] in ("paid", "confirmed"):
                raise ValidationError(
                    "cannot supersede a paid reservation; use the amendment flow instead"
                )
            self.repo.update_reservation(
                reservation["reservation_id"], status=ReservationStatus.SUPERSEDED.value
            )
            self.repo.audit(
                "reservation_superseded",
                "reservation",
                reservation["reservation_id"],
                "ok",
                event_id=reservation["event_id"],
                metadata={"reason": msg.reason[:80]},
            )
            response = ReservationSupersedeResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=reservation["event_id"],
                reservation_id=reservation["reservation_id"],
                reservation_status=ReservationStatus.SUPERSEDED.value,
                side_effect=ExternalEffect.COMPLETED.value,
            )
        except EventPilotError as err:
            response = ReservationSupersedeResponse.failure(
                msg.request_id, err, correlation_id=msg.correlation_id, event_id=msg.event_id
            )
        result.send("coordinator", response)

    def _on_payment_status_request(self, msg: PaymentStatusRequest, result: TurnResult) -> None:
        try:
            reservation = self.repo.get_reservation(msg.reservation_id)
            quote = self.repo.get_quote(msg.quote_id)
            if reservation["quote_id"] != msg.quote_id:
                raise ValidationError("payment does not reference the reservation's quote")
            if quote["event_id"] != msg.event_id:
                raise ValidationError("payment event mismatch")

            expectation_expected_amount = quote["deposit_fet_base"]
            if reservation["status"] in ("paid", "confirmed"):
                # idempotent replay of an already-settled payment
                prior = self.repo.get_payment(msg.payment_id)
                if prior and prior["verification_status"] in ("verified", "verified_simulated"):
                    response = PaymentStatusResponse(
                        request_id=msg.request_id,
                        correlation_id=msg.correlation_id,
                        event_id=msg.event_id,
                        payment_id=msg.payment_id,
                        reservation_id=reservation["reservation_id"],
                        verification_status=prior["verification_status"],
                        simulated=prior.get("payment_mode") == PaymentMode.SIMULATED.value,
                        side_effect=ExternalEffect.SIMULATED.value
                        if prior.get("payment_mode") == PaymentMode.SIMULATED.value
                        else ExternalEffect.COMPLETED.value,
                    )
                    result.send("coordinator", response)
                    return

            self.repo.update_reservation(
                reservation["reservation_id"], status=ReservationStatus.PAYMENT_PENDING.value
            )
            from payments.verification import PaymentEvidence, PaymentExpectation

            expectation = PaymentExpectation(
                quote_id=quote["quote_id"],
                reservation_id=reservation["reservation_id"],
                amount_attofet=expectation_expected_amount,
                asset=msg.asset,
                network=self.payment_network,
                recipient_address=self.supplier_wallet_address,
            )
            evidence = PaymentEvidence(
                transaction_id=msg.transaction_id,
                amount_attofet=msg.amount_fet_base,
                asset=msg.asset,
                network=msg.network,
                recipient_address=msg.recipient_address,
            )
            if expectation_expected_amount == 0:
                # Zero-delta amendment: nothing can move, so the evidence check
                # is trivially satisfied (recorded honestly).
                verdict = __import__(
                    "payments.verification", fromlist=["VerificationResult"]
                ).VerificationResult(
                    "verified_simulated",
                    "zero-delta amendment; no transfer was required",
                    simulated=True,
                )
            else:
                verdict = self.verifier.verify(expectation, evidence)
            ts = now_iso()
            self.repo.save_payment(
                {
                    "payment_id": msg.payment_id,
                    "reservation_id": reservation["reservation_id"],
                    "quote_id": quote["quote_id"],
                    "event_id": msg.event_id,
                    "expected_amount_fet_base": expectation_expected_amount,
                    "asset": msg.asset,
                    "network": msg.network,
                    "recipient_address": self.supplier_wallet_address,
                    "transaction_id": msg.transaction_id,
                    "verification_status": verdict.status,
                    "payment_mode": PaymentMode.SIMULATED.value if verdict.simulated else PaymentMode.TESTNET.value,
                    "created_at": ts,
                    "verified_at": ts
                    if verdict.status in ("verified", "verified_simulated")
                    else None,
                }
            )
            if verdict.status in ("verified", "verified_simulated"):
                self.repo.update_reservation(
                    reservation["reservation_id"],
                    status=ReservationStatus.CONFIRMED.value,  # demo inventory confirmation
                )
                self.repo.audit(
                    "payment_verified",
                    "payment",
                    msg.payment_id,
                    verdict.status,
                    event_id=msg.event_id,
                    metadata={"demo_inventory_confirmation": True},
                )
            elif verdict.status == "rejected":
                self.repo.update_reservation(
                    reservation["reservation_id"],
                    status=ReservationStatus.PAYMENT_FAILED.value,
                )
                self.repo.audit(
                    "payment_rejected",
                    "payment",
                    msg.payment_id,
                    "rejected",
                    event_id=msg.event_id,
                    metadata={"reason": verdict.reason[:100]},
                )
            else:
                self.repo.audit(
                    "payment_unknown",
                    "payment",
                    msg.payment_id,
                    "unknown",
                    event_id=msg.event_id,
                    metadata={"reason": verdict.reason[:100]},
                )

            response = PaymentStatusResponse(
                request_id=msg.request_id,
                correlation_id=msg.correlation_id,
                event_id=msg.event_id,
                payment_id=msg.payment_id,
                reservation_id=reservation["reservation_id"],
                verification_status=verdict.status,
                simulated=verdict.simulated,
                explorer_url=verdict.explorer_url,
                reason=verdict.reason,
                status=ResponseStatus.SUCCESS.value
                if verdict.status in ("verified", "verified_simulated")
                else (
                    ResponseStatus.PENDING.value
                    if verdict.status == "unknown"
                    else ResponseStatus.REJECTED.value
                ),
                side_effect=ExternalEffect.SIMULATED.value
                if verdict.status == "verified_simulated"
                else (
                    ExternalEffect.COMPLETED.value
                    if verdict.status == "verified"
                    else ExternalEffect.UNCERTAIN.value
                    if verdict.status == "unknown"
                    else ExternalEffect.NONE.value
                ),
            )
        except EventPilotError as err:
            self.repo.audit(
                "payment_check_failed", "payment", msg.payment_id, err.code, event_id=msg.event_id
            )
            response = PaymentStatusResponse.failure(
                msg.request_id, err, correlation_id=msg.correlation_id, event_id=msg.event_id
            )
        result.send("coordinator", response)
