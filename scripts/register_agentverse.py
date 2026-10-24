"""Agentverse registration helper for the EventPilot coordinator.

Run this **once** (per coordinator refresh) from a machine that can reach
the deployed agent endpoint. It registers the same payload the coordinator
attempts at startup, and returns clear HTTP status + body so you can see
exactly what Agentverse rejected.

Usage:
    export AGENTVERSE_API_KEY=<your-token>
    python -m scripts.register_agentverse

Required environment variables:
  AGENTVERSE_API_KEY
  AGENT_ADDRESS / COORDINATOR_ADDRESS  (defaults to the deployed coordinator address)
  AGENT_ENDPOINT (defaults to the deployed /submit URL)
"""
from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    token = os.environ.get("AGENTVERSE_API_KEY", "").strip()
    if not token:
        print("AGENTVERSE_API_KEY is required", file=sys.stderr)
        return 2

    address = (
        os.environ.get("AGENT_ADDRESS", "").strip()
        or os.environ.get("COORDINATOR_ADDRESS", "").strip()
        or "agent1qf04lg3ffym4s4ghm8herjl5dvgvfyy33303wgk6a27tzadkk39rcqyf784"
    )

    base_url = os.environ.get("AGENTVERSE_API_URL", "https://agentverse.ai/v2").rstrip("/")
    name = os.environ.get("AGENT_NAME", "eventpilot_coordinator").strip()
    endpoint = os.environ.get(
        "AGENT_ENDPOINT",
        "https://eventpilot-8pjc.onrender.com/submit",
    ).strip()

    if not endpoint.rstrip("/").endswith("/submit"):
        print(
            "AGENT_ENDPOINT must end with /submit (it is the uAgents envelope endpoint)",
            file=sys.stderr,
        )
        return 2

    payload = {
        "address": address,
        "name": name,
        "agent_type": "uagent",
        "url": endpoint,
        "endpoints": [
            {
                "url": endpoint,
                "weight": 1,
            }
        ],
    }

    print("Registering coordinator in Agentverse:", file=sys.stderr)
    print(f"  base_url : {base_url}", file=sys.stderr)
    print(f"  agent    : {address}", file=sys.stderr)
    print(f"  endpoint : {endpoint}", file=sys.stderr)
    print(f"  token    : {'set' if token else 'missing'}", file=sys.stderr)

    try:
        response = httpx.post(
            f"{base_url}/agents",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=30,
        )
    except httpx.HTTPError as exc:
        print(f"Agentverse request failed: {exc}", file=sys.stderr)
        return 1

    body = response.text[:800]
    print(f"HTTP {response.status_code}", file=sys.stderr)
    print(body, file=sys.stderr)

    if response.is_error:
        print("Agentverse rejected registration.", file=sys.stderr)
        return 1

    print("Agentverse registration accepted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
