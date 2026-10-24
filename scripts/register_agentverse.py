"""Register the EventPilot coordinator listing with Agentverse.

This is a one-time/manual operation. The Agentverse token is read from the
environment and is never logged or persisted. It does not belong in the
deployed uAgent runtime.

Required environment variables:
  AGENTVERSE_API_KEY
  COORDINATOR_ADDRESS (or AGENT_ADDRESS)

Optional:
  AGENTVERSE_API_URL (defaults to the v2 API)
  AGENT_NAME (defaults to eventpilot_coordinator)
  AGENT_ENDPOINT (defaults to the public /submit endpoint)
"""

from __future__ import annotations

import os
import sys

import httpx


def main() -> int:
    token = os.environ.get("AGENTVERSE_API_KEY", "").strip()
    address = (
        os.environ.get("COORDINATOR_ADDRESS", "").strip()
        or os.environ.get("AGENT_ADDRESS", "").strip()
    )
    if not token:
        print("AGENTVERSE_API_KEY is required", file=sys.stderr)
        return 2
    if not address:
        print("COORDINATOR_ADDRESS or AGENT_ADDRESS is required", file=sys.stderr)
        return 2

    base_url = os.environ.get("AGENTVERSE_API_URL", "https://agentverse.ai/v2").rstrip("/")
    name = os.environ.get("AGENT_NAME", "eventpilot_coordinator").strip()
    endpoint = os.environ.get(
        "AGENT_ENDPOINT", "https://eventpilot-8pjc.onrender.com/submit"
    ).strip()
    payload = {
        "address": address,
        "name": name,
        "agent_type": "uagent",
        "endpoints": [
            {
                "url": endpoint,
                "protocol": "AgentChatProtocol",
                "weight": 1,
            }
        ],
    }

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

    if response.is_error:
        print(f"Agentverse rejected registration ({response.status_code})", file=sys.stderr)
        print(response.text, file=sys.stderr)
        return 1

    print("Agentverse registration request accepted.")
    print(f"Agent address: {address}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
