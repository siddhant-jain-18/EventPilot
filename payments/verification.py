"""Independent payment verification (spec section 11).

Two verifiers share one interface:

- :class:`SimulatedVerifier` validates evidence against the file-backed
  simulated chain (authoritative for demo mode) plus supplier-side replay
  protection. Results are always labelled verified_simulated.
- :class:`RestChainVerifier` checks REAL testnet evidence against a Cosmos
  REST/LCD endpoint (transaction lookup by hash, transfer event parsing).
  It is the integration point for the official FET Payment Protocol flow;
  configure PAYMENT_RPC_URL to enable it. Unknown network state returns
  status 'unknown' — never a silent failure or a fabricated success.

Verification checklist (both verifiers): correct network, correct asset,
correct recipient, correct amount, settled/success status, evidence present,
transaction id not consumed before, and association with the intended quote.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol as _Protocol

from shared.errors import ValidationError
from shared.time_utils import now_iso
from shared.validation import validate_paise


@dataclass
class PaymentExpectation:
    """What the supplier believes it is owed (from its own quote record)."""

    quote_id: str
    reservation_id: str
    amount_attofet: int
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""


@dataclass
class PaymentEvidence:
    """What the buyer claims happened. Claims are NOT trusted on their own."""

    transaction_id: str
    amount_attofet: int = 0
    asset: str = "atestfet"
    network: str = "simulated"
    recipient_address: str = ""


@dataclass
class VerificationResult:
    status: str  # verified | verified_simulated | rejected | unknown
    reason: str = ""
    simulated: bool = False
    explorer_url: Optional[str] = None


class Verifier(_Protocol):
    def verify(
        self, expectation: PaymentExpectation, evidence: PaymentEvidence
    ) -> VerificationResult: ...


class SimulatedVerifier:
    """Verify simulated transfers against the simulated chain + replay table."""

    def __init__(self, chain, consume_tx_id):
        self.chain = chain
        self.consume_tx_id = consume_tx_id  # callable(tx_id, used_for) -> bool

    def verify(
        self, expectation: PaymentExpectation, evidence: PaymentEvidence
    ) -> VerificationResult:
        amount = validate_paise(evidence.amount_attofet, field="evidence.amount")
        expected = validate_paise(expectation.amount_attofet, field="expected.amount")
        if amount != expected:
            return VerificationResult(
                "rejected",
                f"amount mismatch: expected {expected} atestfet, evidence says {amount}",
                simulated=True,
            )
        if evidence.asset != expectation.asset:
            return VerificationResult("rejected", f"asset mismatch: {evidence.asset}", simulated=True)
        if evidence.network != expectation.network:
            return VerificationResult(
                "rejected", f"network mismatch: {evidence.network}", simulated=True
            )
        if expectation.recipient_address and evidence.recipient_address != expectation.recipient_address:
            return VerificationResult(
                "rejected", "recipient mismatch against supplier wallet", simulated=True
            )
        transfer = self.chain.get_transfer(evidence.transaction_id)
        if transfer is None:
            return VerificationResult(
                "unknown", "transaction not found in simulated chain", simulated=True
            )
        if transfer.get("status") != "settled":
            return VerificationResult(
                "unknown", f"transfer status {transfer.get('status')}", simulated=True
            )
        if int(transfer.get("amount_attofet") or 0) != expected:
            return VerificationResult(
                "rejected", "on-record amount differs from the expected amount", simulated=True
            )
        if transfer.get("to") != expectation.recipient_address:
            return VerificationResult("rejected", "on-record recipient differs", simulated=True)
        if transfer.get("quote_ref") != expectation.quote_id:
            return VerificationResult(
                "rejected", "transfer is not associated with the intended quote", simulated=True
            )
        if not self.consume_tx_id(evidence.transaction_id, expectation.reservation_id):
            return VerificationResult(
                "rejected", "transaction id was already consumed (replay)", simulated=True
            )
        return VerificationResult(
            "verified_simulated",
            "transfer verified against simulated chain (no on-chain payment occurred)",
            simulated=True,
        )


class RestChainVerifier:
    """Verify a real Fetch.ai testnet transfer via a Cosmos REST/LCD endpoint.

    The lookup is ``GET {rpc}/cosmos/tx/v1beta1/txs/{tx_hash}``; transfer
    events are parsed from the response's logs. Configuration and the full
    buyer/seller protocol flow follow the official FET payment example —
    see payments/README.md before enabling PAYMENT_MODE=testnet.
    """

    def __init__(self, rpc_url: str, network: str, consume_tx_id, http_client=None, explorer_base: str = ""):
        if not rpc_url:
            raise ValidationError("PAYMENT_RPC_URL is required for testnet verification")
        self.rpc_url = rpc_url.rstrip("/")
        self.network = network
        self.consume_tx_id = consume_tx_id
        self._client = http_client  # injectable for tests (httpx.MockTransport)
        self.explorer_base = explorer_base

    def verify(
        self, expectation: PaymentExpectation, evidence: PaymentEvidence
    ) -> VerificationResult:
        import httpx  # local import: only needed in real mode

        if evidence.network != self.network:
            return VerificationResult("rejected", f"expected network {self.network}, got {evidence.network}")
        if evidence.asset != expectation.asset:
            return VerificationResult("rejected", f"expected asset {expectation.asset}")
        client = self._client or httpx.Client(timeout=10)
        try:
            response = (
                client.get(f"{self.rpc_url}/cosmos/tx/v1beta1/txs/{evidence.transaction_id}")
                if self._client
                else httpx.get(
                    f"{self.rpc_url}/cosmos/tx/v1beta1/txs/{evidence.transaction_id}", timeout=10
                )
            )
        except Exception as err:  # network errors -> unknown, never a guess
            return VerificationResult("unknown", f"chain query failed: {err}")
        finally:
            if self._client is None:
                try:
                    client.close()
                except Exception:
                    pass
        if response.status_code == 404:
            return VerificationResult("unknown", "transaction not found (may still be pending)")
        if response.status_code != 200:
            return VerificationResult("unknown", f"chain query HTTP {response.status_code}")
        try:
            data = response.json()
        except Exception:
            return VerificationResult("unknown", "unparseable chain response")

        tx = data.get("tx_response") or {}
        if str(tx.get("code", "1")) != "0":
            return VerificationResult("rejected", f"transaction failed on chain (code {tx.get('code')})")
        transfers = _parse_transfers(data)
        matched = [
            t for t in transfers if t["recipient"] == expectation.recipient_address
        ]
        if not matched:
            return VerificationResult("rejected", "no transfer to the expected recipient found in tx events")
        amounts = _sum_amounts(matched, expectation.asset)
        if amounts != expectation.amount_attofet:
            return VerificationResult(
                "rejected",
                f"amount mismatch: expected {expectation.amount_attofet}, chain shows {amounts}",
            )
        if not self.consume_tx_id(evidence.transaction_id, expectation.reservation_id):
            return VerificationResult("rejected", "transaction id already consumed (replay)")
        explorer = (
            f"{self.explorer_base}/tx/{evidence.transaction_id}" if self.explorer_base else None
        )
        return VerificationResult("verified", "transaction verified against chain data", explorer_url=explorer)


def _parse_transfers(tx_json: dict) -> list[dict]:
    """Extract (recipient, amount, denom) triples from coin_received events.

    Cosmos amounts are concatenated strings like ``1000000atestfet``.
    """
    import re

    transfers: list[dict] = []
    logs = (tx_json.get("tx_response") or {}).get("logs") or []
    for log in logs:
        for event in log.get("events") or []:
            if event.get("type") != "coin_received":
                continue
            attrs = {a.get("key"): a.get("value") for a in event.get("attributes") or []}
            receiver = attrs.get("receiver", "")
            for amount_str in (attrs.get("amount") or "").split(","):
                for part in amount_str.strip().split(" "):
                    if not part:
                        continue
                    match = re.match(r"^(\d+)([a-zA-Z].*)$", part)
                    if match:
                        transfers.append(
                            {
                                "recipient": receiver,
                                "amount": int(match.group(1)),
                                "denom": match.group(2),
                            }
                        )
    return transfers


def _sum_amounts(transfers: list[dict], denom: str) -> int:
    return sum(t["amount"] for t in transfers if t["denom"] == denom and t["amount"] > 0)
