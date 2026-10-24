"""Payment protocol role definitions.

The official Fetch.ai FET payment example defines a buyer/seller message
lifecycle. This project maps its stages onto the inter-agent contracts in
`shared/messages.py` so the flow stays honest and inspectable:

| Official stage  | EventPilot contract                    | Owner     |
|-----------------|-------------------------------------------|-----------|
| quote           | QuoteResponse.payment terms (deposit)     | supplier  |
| RequestPayment  | ReservationResponse.payment_requirement   | supplier  |
| CommitPayment   | PaymentStatusRequest (with tx evidence)   | coordinator |
| verification    | payments/verification.py (independent)    | supplier  |
| CompletePayment | PaymentStatusResponse(verified*)          | supplier  |
| CancelPayment   | PaymentStatusResponse(rejected/failed)    | supplier  |

PAYMENT_MODE=simulated (default, and the mode actually used by this build):
no on-chain transfer occurs; the simulated chain in payments/ledger.py is the
authoritative demo record and every status is labelled. See payments/README.md
for the exact steps needed to enable testnet mode.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PaymentQuote:
    """Fixed deposit terms attached to a quote (supplier authority)."""

    quote_id: str
    amount_attofet: int
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""
    expires_at: str = ""


@dataclass
class PaymentAttemptRecord:
    """Persisted BEFORE any irreversible step (idempotency-first)."""

    payment_id: str
    reservation_id: str
    quote_id: str
    expected_amount_attofet: int
    status: str = "created"
    transaction_id: str = ""
    simulated: bool = True
