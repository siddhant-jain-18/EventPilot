# Payments

## Modes

| Mode | What actually happens | How it is labelled |
|---|---|---|
| `simulated` (default, **used by this build**) | The coordinator's demo wallet appends a transfer record to the file-backed simulated chain (`data/simulated_chain.jsonl`). The supplier **independently verifies** the record: recipient, exact amount, denom, settled status, quote association, and replay protection via `txn_replay`. No on-chain transfer occurs. | Every status line says **SIMULATED — NO ON-CHAIN PAYMENT**; verification status is `verified_simulated`; tx ids are prefixed `SIMULATED-`. |
| `testnet` | Requires the official FET Payment Protocol buyer/seller flow and an RPC endpoint (`PAYMENT_RPC_URL`). The supplier verifies evidence against chain data via `RestChainVerifier` before any status changes. | `verified` only after independent on-chain verification. |

## Hard rules (enforced in code)

1. A payment is never marked verified because a request was *sent*.
2. The supplier never trusts the coordinator's claim — it verifies evidence itself.
3. A transaction id can be consumed once (`txn_replay` table); replays are rejected.
4. Unknown settlement (timeout, chain query failure, 404) maps to status `unknown`;
   the operation is reconciled with evidence, never blindly retried.
5. Simulated and real records never mix: `payment_mode` and `verification_status`
   carry the distinction, and simulated ids are structurally recognisable.
6. Spend caps: `MAX_TRANSFER_FET` (default 0.2 tFET) per transfer and
   `PER_EVENT_MAX_FET` (default 0.6 tFET) per event, enforced in `payments/ledger.py`.
   The demo wallet is funded with `SIMULATED_COORDINATOR_BALANCE_FET` (default 1.0 tFET).

## INR vs tFET

INR amounts are the **event expense** the budget governs (integer paise).
tFET deposits are a **symbolic reservation deposit on a testnet** that proves
the payment rail. There is **no exchange rate** between them and the code
never converts one into the other.

## Enabling testnet mode (checklist)

1. Follow the official FET payment example from the Fetch.ai docs to pin its
   dependency set; do not invent SDK methods.
2. Fund a dedicated testnet wallet from the faucet (faucets can be slow — do
   this first).
3. Set `PAYMENT_MODE=testnet`, `PAYMENT_NETWORK`, `PAYMENT_RPC_URL`,
   `SUPPLIER_WALLET_ADDRESS`.
4. Run the payment tests with a mocked chain client first
   (`tests/unit/test_payment_verification.py`) — they cover wrong recipient,
   wrong amount, wrong asset, unconfirmed transactions, replays and timeouts.
5. Only then enable live settlement and record the transaction evidence in the
   audit timeline.

Until those steps are completed and verified live, the build stays in
`simulated` mode and says so everywhere the user can see it.
