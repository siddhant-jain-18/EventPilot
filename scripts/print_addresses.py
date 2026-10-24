"""Compute uAgents addresses from the configured seeds (requires uagents).

Run:  python -m scripts.print_addresses
"""

from __future__ import annotations

import sys

from shared.config import load_settings


def main() -> int:
    try:
        from uagents import Agent
    except ImportError:
        print("uagents is not installed: pip install -r requirements.txt")
        return 1
    settings = load_settings()
    for role, seed_env, name in (
        ("COORDINATOR", settings.coordinator_seed, "eventpilot_coordinator"),
        ("SUPPLIER", settings.supplier_seed, "eventpilot_supplier"),
        ("OUTREACH", settings.outreach_seed, "eventpilot_outreach"),
    ):
        if not seed_env:
            print(f"{role}: SEED MISSING - set {'_'.join([role, 'SEED'])} in .env")
            continue
        agent = Agent(name=name, seed=seed_env, resolve=False)
        print(f"{role} address: {agent.address}")
    print(
        "\nCopy the SUPPLIER and OUTREACH addresses into SUPPLIER_ADDRESS / OUTREACH_ADDRESS "
        "in .env so the coordinator can reach them."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
