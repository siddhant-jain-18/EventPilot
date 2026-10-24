"""Optional live smoke test: constructs real uAgents identities locally.

Skipped unless RUN_AGENT_TESTS=1 (requires the uagents install; no internet
needed). Verifies that deterministic seeds produce distinct, stable agent1q...
addresses — the identity layer of the three-agent deployment.

    RUN_AGENT_TESTS=1 python -m pytest tests/protocol/test_local_agents.py -m agent_network
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.agent_network,
    pytest.mark.skipif(
        os.environ.get("RUN_AGENT_TESTS") != "1",
        reason="set RUN_AGENT_TESTS=1 to run the live local-agent smoke test",
    ),
]


def test_agent_identities_are_deterministic_and_distinct():
    try:
        from uagents import Agent
    except ImportError:
        pytest.skip("uagents not installed")
    supplier = Agent(name="eventpilot_supplier", seed="seed-aaa-0001", resolve=False)
    supplier_again = Agent(name="eventpilot_supplier", seed="seed-aaa-0001", resolve=False)
    coordinator = Agent(name="eventpilot_coordinator", seed="seed-bbb-0001", resolve=False)
    assert supplier.address == supplier_again.address, "same name+seed must give the same address"
    assert supplier.address != coordinator.address
    assert supplier.address.startswith("agent1q")
