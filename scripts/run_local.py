"""Start the full local stack: 3 agents + RSVP service.

Run:  python -m scripts.run_local

Requires SUPPLIER_ADDRESS and OUTREACH_ADDRESS in .env (see
scripts/print_addresses.py) and seeded data (scripts/seed_demo_data.py).
Ctrl+C stops everything gracefully.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

from shared.config import load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]

PROCESSES: list[dict] = [
    {"name": "supplier", "module": "agents.supplier.agent", "env": {"AGENT_PORT": "8102"}},
    {"name": "outreach", "module": "agents.outreach.agent", "env": {"AGENT_PORT": "8103"}},
    {"name": "coordinator", "module": "agents.coordinator.agent", "env": {"AGENT_PORT": "8101"}},
    {
        "name": "rsvp",
        "module": "uvicorn",
        "args": ["rsvp_web.app:app_from_env()", "--host", "0.0.0.0", "--port", "${RSVP_PORT}"],
    },
]


def main() -> int:
    settings = load_settings()
    if not settings.supplier_address or not settings.outreach_address:
        print(
            "SUPPLIER_ADDRESS / OUTREACH_ADDRESS are missing.\n"
            "Run: python -m scripts.print_addresses  and copy them into .env first."
        )
        return 1
    children: list[subprocess.Popen] = []

    def _shutdown(*_args):
        print("\nShutting down...")
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        import uagents  # noqa: F401
    except ImportError:
        print("uagents is not installed: pip install -r requirements.txt")
        return 1

    for spec in PROCESSES:
        env = os.environ.copy()
        env.update({k: v for k, v in spec.get("env", {}).items()})
        if spec["module"] == "uvicorn":
            args = [
                "rsvp_web.app:app_from_env()",
                "--host",
                "0.0.0.0",
                "--port",
                str(settings.rsvp_port),
            ]
            child = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", *args], cwd=REPO_ROOT, env=env
            )
        else:
            child = subprocess.Popen(
                [sys.executable, "-m", spec["module"]], cwd=REPO_ROOT, env=env
            )
        children.append(child)
        print(f"started {spec['name']} (pid {child.pid})")

    print(
        "\nLocal stack running:"
        f"\n  coordinator : http://127.0.0.1:8101 (AGENT_MAILBOX={os.environ.get('AGENT_MAILBOX', 'false')})"
        f"\n  supplier    : http://127.0.0.1:8102"
        f"\n  outreach    : http://127.0.0.1:8103"
        f"\n  rsvp        : http://127.0.0.1:{settings.rsvp_port}"
        "\nKeep this process alive for ASI:One/Agentverse to reach the coordinator. Ctrl+C stops all."
    )
    try:
        for child in children:
            child.wait()
    except KeyboardInterrupt:
        _shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
