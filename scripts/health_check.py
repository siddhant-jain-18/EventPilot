"""Health / readiness checks for local runs.

Run:  python -m scripts.health_check
Exit code 0 = healthy, 1 = problems (list printed).
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from shared.config import load_settings


def main() -> int:
    settings = load_settings()
    problems: list[str] = []
    for name in ("coordinator.db", "supplier.db", "outreach.db"):
        path = settings.database_path(name)
        if not path.exists():
            problems.append(f"missing database {path} (run scripts/seed_demo_data.py)")
    if not settings.allowed_test_recipients:
        problems.append("ALLOWED_TEST_RECIPIENTS is empty (email sends are impossible)")
    if settings.email_provider == "smtp" and not settings.email_smtp_host:
        problems.append("EMAIL_PROVIDER=smtp but EMAIL_SMTP_HOST is unset")
    if settings.llm_enabled:
        print(f"ASI1 LLM: enabled (model {settings.asi1_model})")
    else:
        print("ASI1 LLM: not configured -> deterministic fallbacks active (honest mode)")
    print(f"Payment mode: {settings.payment_mode}" + (
        " (SIMULATED - NO ON-CHAIN PAYMENT)" if settings.payment_mode == "simulated" else ""
    ))
    print(f"Email provider: {settings.email_provider}")
    for peer in ("supplier_address", "outreach_address"):
        if not getattr(settings, peer):
            problems.append(f"{peer.upper()} is not set (coordinator cannot reach its peer)")
    rsvp_url = settings.rsvp_base_url.rstrip("/") + "/health"
    try:
        response = httpx.get(rsvp_url, timeout=3)
        print(f"RSVP service {rsvp_url}: HTTP {response.status_code}")
    except Exception as err:
        print(f"RSVP service {rsvp_url}: not reachable ({err.__class__.__name__})")
    for addr_file in ("coordinator.address", "supplier.address", "outreach.address"):
        path = settings.database_path(addr_file)
        if path.exists():
            print(f"{addr_file}: {path.read_text().strip()}")
    if problems:
        print("\nPROBLEMS:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("\nAll static checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
