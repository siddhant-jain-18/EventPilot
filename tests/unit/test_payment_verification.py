"""Payment verification: simulated ledger rules and chain verifier."""

from __future__ import annotations

import httpx
import pytest

from payments.ledger import SimulatedChain
from payments.verification import (
    PaymentEvidence,
    PaymentExpectation,
    RestChainVerifier,
    SimulatedVerifier,
)
from shared.errors import BudgetExceededError


ATESTFET = 10**18
EXPECT = PaymentExpectation(
    quote_id="Q-1",
    reservation_id="R-1",
    amount_attofet=13 * 10**16,
    asset="atestfet",
    network="simulated",
    recipient_address="wp-demo-supplier-wallet",
)


@pytest.fixture()
def chain(tmp_path):
    c = SimulatedChain(tmp_path / "chain.jsonl")
    c.open_account("wp-demo-coordinator-wallet", "1.0")
    return c


def test_transfer_and_balance(chain):
    tx = chain.pay(
        from_address="wp-demo-coordinator-wallet",
        to_address="wp-demo-supplier-wallet",
        amount_attofet=13 * 10**16,
        event_id="E-1",
        quote_ref="Q-1",
        max_transfer_attofet=2 * 10**17,
        per_event_max_attofet=6 * 10**17,
    )
    assert tx["tx_id"].startswith("SIMULATED-")
    assert chain.balance("wp-demo-coordinator-wallet") == ATESTFET - 13 * 10**16
    assert chain.balance("wp-demo-supplier-wallet") == 13 * 10**16


def test_per_transfer_cap(chain):
    with pytest.raises(BudgetExceededError):
        chain.pay(
            from_address="wp-demo-coordinator-wallet",
            to_address="wp-demo-supplier-wallet",
            amount_attofet=3 * 10**17,
            event_id="E-1",
            quote_ref="Q-1",
            max_transfer_attofet=2 * 10**17,
            per_event_max_attofet=6 * 10**17,
        )


def test_per_event_cap(chain):
    for _ in range(4):
        chain.pay(
            from_address="wp-demo-coordinator-wallet",
            to_address="wp-demo-supplier-wallet",
            amount_attofet=15 * 10**16,
            event_id="E-1",
            quote_ref=f"Q-{_}",
            max_transfer_attofet=2 * 10**17,
            per_event_max_attofet=6 * 10**17,
        )
    with pytest.raises(BudgetExceededError):
        chain.pay(
            from_address="wp-demo-coordinator-wallet",
            to_address="wp-demo-supplier-wallet",
            amount_attofet=15 * 10**16,
            event_id="E-1",
            quote_ref="Q-x",
            max_transfer_attofet=2 * 10**17,
            per_event_max_attofet=6 * 10**17,
        )


def test_insufficient_balance(chain):
    with pytest.raises(BudgetExceededError):
        chain.pay(
            from_address="wp-demo-coordinator-wallet",
            to_address="wp-demo-supplier-wallet",
            amount_attofet=11 * 10**17,  # wallet holds 1.0
            event_id="E-1",
            quote_ref="Q-1",
            max_transfer_attofet=12 * 10**17,
            per_event_max_attofet=12 * 10**17,
        )


def _consumed_set():
    return set()


def test_simulated_verifier_happy_path(chain):
    tx = chain.pay(
        from_address="wp-demo-coordinator-wallet",
        to_address="wp-demo-supplier-wallet",
        amount_attofet=13 * 10**16,
        event_id="E-1",
        quote_ref="Q-1",
        max_transfer_attofet=2 * 10**17,
        per_event_max_attofet=6 * 10**17,
    )
    used: set[str] = set()
    verifier = SimulatedVerifier(
        chain=chain, consume_tx_id=lambda tx_id, used_for: used.add(tx_id) or True
    )
    verdict = verifier.verify(
        EXPECT,
        PaymentEvidence(
            transaction_id=tx["tx_id"],
            amount_attofet=13 * 10**16,
            asset="atestfet",
            network="simulated",
            recipient_address="wp-demo-supplier-wallet",
        ),
    )
    assert verdict.status == "verified_simulated"
    assert verdict.simulated


@pytest.mark.parametrize(
    "evidence",
    [
        PaymentEvidence(transaction_id="SIMULATED-NOPE", amount_attofet=13 * 10**16, network="simulated", recipient_address="wp-demo-supplier-wallet"),
        PaymentEvidence(transaction_id="SIMULATED-X", amount_attofet=1, network="simulated", recipient_address="wp-demo-supplier-wallet"),
        PaymentEvidence(transaction_id="SIMULATED-X", amount_attofet=13 * 10**16, network="simulated", recipient_address="mallory-wallet"),
        PaymentEvidence(transaction_id="SIMULATED-X", amount_attofet=13 * 10**16, network="mainnet", recipient_address="wp-demo-supplier-wallet"),
        PaymentEvidence(transaction_id="SIMULATED-X", amount_attofet=13 * 10**16, asset="uatom", network="simulated", recipient_address="wp-demo-supplier-wallet"),
    ],
    ids=["unknown-tx", "wrong-amount", "wrong-recipient", "wrong-network", "wrong-asset"],
)
def test_simulated_verifier_rejects_bad_evidence(chain, evidence):
    # seed one real transfer for the non-unknown-tx cases
    chain.pay(
        from_address="wp-demo-coordinator-wallet",
        to_address="wp-demo-supplier-wallet",
        amount_attofet=13 * 10**16,
        event_id="E-1",
        quote_ref="Q-1",
        max_transfer_attofet=2 * 10**17,
        per_event_max_attofet=6 * 10**17,
    )
    used: set[str] = set()
    verifier = SimulatedVerifier(
        chain=chain, consume_tx_id=lambda tx_id, used_for: used.add(tx_id) or True
    )
    verdict = verifier.verify(EXPECT, evidence)
    if evidence.transaction_id == "SIMULATED-NOPE":
        assert verdict.status == "unknown"
    else:
        assert verdict.status == "rejected"
    assert verdict.status != "verified_simulated"


def test_simulated_verifier_replay_rejected(chain):
    tx = chain.pay(
        from_address="wp-demo-coordinator-wallet",
        to_address="wp-demo-supplier-wallet",
        amount_attofet=13 * 10**16,
        event_id="E-1",
        quote_ref="Q-1",
        max_transfer_attofet=2 * 10**17,
        per_event_max_attofet=6 * 10**17,
    )
    used: set[str] = set()

    def consume(tx_id, used_for):
        if tx_id in used:
            return False
        used.add(tx_id)
        return True

    verifier = SimulatedVerifier(chain=chain, consume_tx_id=consume)
    evidence = PaymentEvidence(
        transaction_id=tx["tx_id"],
        amount_attofet=13 * 10**16,
        network="simulated",
        recipient_address="wp-demo-supplier-wallet",
    )
    assert verifier.verify(EXPECT, evidence).status == "verified_simulated"
    assert verifier.verify(EXPECT, evidence).status == "rejected"  # replayed tx id


def _chain_json(code=0, recipient="wp-demo-supplier-wallet", amount="130000000000000000atestfet", status=200):
    body = {
        "tx_response": {
            "code": code,
            "logs": [
                {
                    "events": [
                        {
                            "type": "coin_received",
                            "attributes": [
                                {"key": "receiver", "value": recipient},
                                {"key": "amount", "value": amount},
                            ],
                        }
                    ]
                }
            ],
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if status == 200 else {})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_rest_verifier_accepts_valid_transaction(tmp_path):
    used: set[str] = set()
    verifier = RestChainVerifier(
        rpc_url="https://rpc.example.test",
        network="dorado-1",
        consume_tx_id=lambda tx_id, used_for: used.add(tx_id) or True,
        http_client=_chain_json(),
    )
    verdict = verifier.verify(
        PaymentExpectation(
            quote_id="Q-1",
            reservation_id="R-1",
            amount_attofet=13 * 10**16,
            asset="atestfet",
            network="dorado-1",
            recipient_address="wp-demo-supplier-wallet",
        ),
        PaymentEvidence(transaction_id="ABC123", asset="atestfet", network="dorado-1"),
    )
    assert verdict.status == "verified"


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"recipient": "mallory-wallet"}, "rejected"),
        ({"amount": "1atestfet"}, "rejected"),
        ({"code": 5}, "rejected"),
        ({"status": 404}, "unknown"),
    ],
)
def test_rest_verifier_rejects_or_marks_unknown(tmp_path, kwargs, expected):
    used: set[str] = set()
    verifier = RestChainVerifier(
        rpc_url="https://rpc.example.test",
        network="dorado-1",
        consume_tx_id=lambda tx_id, used_for: used.add(tx_id) or True,
        http_client=_chain_json(**kwargs),
    )
    verdict = verifier.verify(
        PaymentExpectation(
            quote_id="Q-1",
            reservation_id="R-1",
            amount_attofet=13 * 10**16,
            asset="atestfet",
            network="dorado-1",
            recipient_address="wp-demo-supplier-wallet",
        ),
        PaymentEvidence(transaction_id="ABC123", asset="atestfet", network="dorado-1"),
    )
    assert verdict.status == expected


def test_rest_verifier_wrong_network_rejected():
    used: set[str] = set()
    verifier = RestChainVerifier(
        rpc_url="https://rpc.example.test",
        network="dorado-1",
        consume_tx_id=lambda tx_id, used_for: used.add(tx_id) or True,
        http_client=_chain_json(),
    )
    verdict = verifier.verify(
        PaymentExpectation(
            quote_id="Q-1",
            reservation_id="R-1",
            amount_attofet=13 * 10**16,
            network="dorado-1",
            recipient_address="wp-demo-supplier-wallet",
        ),
        PaymentEvidence(transaction_id="ABC123", network="somewhere-else"),
    )
    assert verdict.status == "rejected"
