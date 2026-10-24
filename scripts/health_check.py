"""Health / readiness checks for local runs.

Run:  python -m scripts.health_check
Exit code 0 = healthy, 1 = problems (list printed).

Checks the RSVP service and the three agent HTTP endpoints, so a failed
deployment is visible instead of silently "running".
"""

from __future__ import annotations

import sys

import httpx

from shared.config import DEFAULT_AGENT_PORTS, load_settings


def main() -> int:
    settings = load_settings()
    problems: list[str] = []
    for name in ("coordinator.db", "supplier.db", "outreach.db"):
        path = settings.database_path(name)
        if not path.exists():
            problems.append(f"missing database {path} (run scripts.seed_demo_data)")
    if not settings.allowed_test_recipients:
        problems.append("ALLOWED_TEST_RECIPIENTS is empty (email sends are impossible)")
    if settings.email_provider == "smtp" and not settings.email_smtp_host:
        problems.append("EMAIL_PROVIDER=smtp but EMAIL_SMTP_HOST is unset")
    if settings.agent_mailbox and settings.agent_endpoint:
        problems.append(
            "AGENT_MAILBOX=true and AGENT_ENDPOINT are mutually exclusive; unset AGENT_ENDPOINT"
        )
    if settings.llm_enabled:
        print(f"ASI1 LLM: enabled (model {settings.asi1_model})")
    else:
        print("ASI1 LLM: not configured -> deterministic fallbacks active (honest mode)")
    print(
        f"Payment mode: {settings.payment_mode}"
        + (
            " (SIMULATED - NO ON-CHAIN PAYMENT)"
            if settings.payment_mode == "simulated"
            else ""
        )
    )
    print(f"Email provider: {settings.email_provider}")
    if settings.agent_mailbox:
        print("Agent mode: mailbox (Agentverse relay)")
    elif settings.agent_endpoint:
        print(f"Agent endpoint: {settings.agent_endpoint}")
    for peer in ("supplier_address", "outreach_address"):
        if not getattr(settings, peer):
            problems.append(f"{peer.upper()} is not set (coordinator cannot reach its peer)")

    rsvp_url = settings.rsvp_base_url.rstrip("/") + "/health"
    try:
        response = httpx.get(rsvp_url, timeout=5)
        print(f"RSVP service {rsvp_url}: HTTP {response.status_code}")
        if response.status_code != 200:
            problems.append(f"RSVP service {rsvp_url} returned HTTP {response.status_code}")
    except Exception as err:
        print(f"RSVP service {rsvp_url}: not reachable ({err.__class__.__name__})")
        problems.append(
            f"RSVP service {rsvp_url} is not reachable "
            "(start scripts.run_local or fix RSVP_BASE_URL)"
        )

    for role, port in DEFAULT_AGENT_PORTS.items():
        url = f"http://127.0.0.1:{port}/submit"
        try:
            response = httpx.get(url, timeout=5)
            print(f"{role} endpoint {url}: HTTP {response.status_code}")
            if response.status_code >= 500:
                problems.append(f"{role} endpoint {url} returned HTTP {response.status_code}")
        except Exception as err:
            print(f"{role} endpoint {url}: not reachable ({err.__class__.__name__})")
            problems.append(
                f"{role} agent is not listening on port {port} (start scripts.run_local)"
            )

    for addr_file in ("coordinator.address", "supplier.address", "outreach.address"):
        path = settings.database_path(addr_file)
        if path.exists():
            print(f"{addr_file}: {path.read_text().strip()}")
    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
