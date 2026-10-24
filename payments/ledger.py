"""Simulated payment chain and ledger (spec section 11 'Explicit fallback').

The simulated chain is a file-backed append-only JSONL ledger that both sides
treat as the authoritative "chain data" for the demo:

- the coordinator appends a transfer record (its demo wallet pays the deposit),
- the supplier INDEPENDENTLY reads the file to verify the transfer evidence —
  correct recipient, exact amount, settled status, and a transaction id that
  was never consumed before (replay protection lives in the supplier DB).

Rules enforced here:
- records are prefixed SIMULATED- and can never be presented as real tx hashes;
- a hard per-transfer cap and a per-event cap bound the demo wallet's exposure;
- balances are enforced (no money appears from nowhere);
- simulated records carry payment_mode=simulated and verification status
  verified_simulated, never 'verified'.
"""

from __future__ import annotations

import json
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional
from uuid import uuid4

from shared.errors import BudgetExceededError, ConfigurationError, ValidationError
from shared.inventory import ATESTFET
from shared.time_utils import now_iso
from shared.validation import validate_paise


def _parse_fet(text: str) -> int:
    try:
        amount = Decimal(str(text))
    except InvalidOperation as err:
        raise ConfigurationError(f"invalid FET amount: {text!r}") from err
    if amount < 0:
        raise ConfigurationError("FET amount must be non-negative")
    return int((amount * ATESTFET).to_integral_value())


class SimulatedChain:
    """File-backed append-only ledger standing in for a testnet chain."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    def _append(self, entry: dict) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")

    def open_account(self, address: str, balance_fet: str | int) -> dict:
        existing = {e.get("address") for e in self._read() if e.get("type") == "open_account"}
        if address in existing:
            return {"address": address, "already_open": True}
        entry = {
            "type": "open_account",
            "address": address,
            "balance_attofet": _parse_fet(balance_fet),
            "ts": now_iso(),
        }
        self._append(entry)
        return entry

    def balance(self, address: str) -> int:
        total = 0
        for entry in self._read():
            if entry.get("type") == "open_account" and entry.get("address") == address:
                total += int(entry.get("balance_attofet") or 0)
            elif entry.get("type") == "transfer" and entry.get("from") == address:
                total -= int(entry.get("amount_attofet") or 0)
            elif entry.get("type") == "transfer" and entry.get("to") == address:
                total += int(entry.get("amount_attofet") or 0)
        return total

    def get_transfer(self, tx_id: str) -> Optional[dict]:
        for entry in self._read():
            if entry.get("type") == "transfer" and entry.get("tx_id") == tx_id:
                return entry
        return None

    def event_total(self, event_id: str) -> int:
        return sum(
            int(e.get("amount_attofet") or 0)
            for e in self._read()
            if e.get("type") == "transfer" and e.get("event_id") == event_id
        )

    def pay(
        self,
        *,
        from_address: str,
        to_address: str,
        amount_attofet: int,
        event_id: str,
        quote_ref: str,
        memo: str = "",
        max_transfer_attofet: int,
        per_event_max_attofet: int,
    ) -> dict:
        """Execute a simulated transfer after enforcing every safety cap."""
        amount_attofet = validate_paise(amount_attofet, field="amount_attofet")
        if amount_attofet <= 0:
            raise ValidationError("transfer amount must be positive")
        if amount_attofet > max_transfer_attofet:
            raise BudgetExceededError(
                "transfer exceeds the per-transfer spend cap "
                f"({_parse_fet(max_transfer_attofet) / ATESTFET} tFET)"
            )
        event_total = self.event_total(event_id)
        if event_total + amount_attofet > per_event_max_attofet:
            raise BudgetExceededError(
                "transfer exceeds the per-event simulated spend cap"
            )
        if self.balance(from_address) < amount_attofet:
            raise BudgetExceededError(
                "coordinator demo wallet has insufficient simulated balance"
            )
        if from_address == to_address:
            raise ValidationError("sender and recipient must differ")
        tx_id = f"SIMULATED-{uuid4().hex[:16].upper()}"
        entry = {
            "type": "transfer",
            "tx_id": tx_id,
            "from": from_address,
            "to": to_address,
            "amount_attofet": amount_attofet,
            "denom": "atestfet",
            "network": "simulated",
            "event_id": event_id,
            "quote_ref": quote_ref,
            "memo": memo[:120],
            "status": "settled",
            "ts": now_iso(),
            "simulated": True,
        }
        self._append(entry)
        return entry


class PaymentCaps:
    def __init__(self, max_transfer_fet: str = "0.2", per_event_max_fet: str = "0.6"):
        self.max_transfer_attofet = _parse_fet(max_transfer_fet)
        self.per_event_max_attofet = _parse_fet(per_event_max_fet)
