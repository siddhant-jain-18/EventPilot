"""Start the full local stack: 3 agents + RSVP service.

Run:  python -m scripts.run_local

Requires seeds, SUPPLIER_ADDRESS / OUTREACH_ADDRESS and RSVP_SIGNING_SECRET in
.env (see scripts/init_env.py). Seeded data comes from scripts.seed_demo_data.
Ctrl+C stops everything gracefully.

AGENT_MAILBOX in .env applies to the coordinator (the ASI:One-facing agent).
The supplier and outreach agents stay on local HTTP endpoints so the three
processes can talk inside one host/container. Set AGENT_MAILBOX=true yourself
when running an individual peer process on a different host.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from shared.config import DEFAULT_AGENT_PORTS, load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def _processes() -> list[dict]:
    return [
        {
            "name": "supplier",
            "module": "agents.supplier.agent",
            # Peers stay on localhost: AGENT_MAILBOX/AGENT_ENDPOINT in .env
            # describe the coordinator's public route, not theirs.
            "env": {
                "AGENT_PORT": str(DEFAULT_AGENT_PORTS["supplier"]),
                "AGENT_MAILBOX": "false",
                "AGENT_ENDPOINT": "",
            },
        },
        {
            "name": "outreach",
            "module": "agents.outreach.agent",
            "env": {
                "AGENT_PORT": str(DEFAULT_AGENT_PORTS["outreach"]),
                "AGENT_MAILBOX": "false",
                "AGENT_ENDPOINT": "",
            },
        },
        {
            "name": "coordinator",
            "module": "agents.coordinator.agent",
            "env": {"AGENT_PORT": str(DEFAULT_AGENT_PORTS["coordinator"])},
        },
        {"name": "rsvp", "module": "uvicorn"},
    ]


def _preflight(settings) -> list[str]:
    problems: list[str] = []
    for name, value in (
        ("COORDINATOR_SEED", settings.coordinator_seed),
        ("SUPPLIER_SEED", settings.supplier_seed),
        ("OUTREACH_SEED", settings.outreach_seed),
        ("SUPPLIER_ADDRESS", settings.supplier_address),
        ("OUTREACH_ADDRESS", settings.outreach_address),
        ("RSVP_SIGNING_SECRET", settings.rsvp_signing_secret),
    ):
        if not value:
            problems.append(f"{name} is missing")
    if not settings.allowed_test_recipients:
        problems.append(
            "ALLOWED_TEST_RECIPIENTS is empty (invitation drafts and sends are refused)"
        )
    problems.extend(settings.validate())
    if settings.agent_mailbox:
        print(
            "Mailbox mode: the coordinator will connect to the Agentverse relay. "
            "Keep this process running for ASI:One to reach it."
        )
    if not settings.agent_mailbox and not settings.agent_endpoint:
        print(
            "Local mode: the coordinator advertises http://127.0.0.1:8101/submit. "
            "Set AGENT_MAILBOX=true (Agentverse) or AGENT_ENDPOINT=https://.../submit "
            "for a public deployment."
        )
    return problems


def main() -> int:
    settings = load_settings()
    # A single-port PaaS (Render/Railway) exposes PORT publicly. Keep the
    # RSVP and agent listeners private and place a gateway on that port so
    # both /submit and RSVP URLs work from the same public origin.
    platform_port = int(os.environ["PORT"]) if os.environ.get("PORT") else None
    single_port = platform_port is not None and not os.environ.get("RSVP_PORT")
    internal_rsvp_port = 8104 if single_port else settings.rsvp_port
    problems = _preflight(settings)
    if problems:
        print("Cannot start the stack:\n- " + "\n- ".join(problems))
        print(
            "\nFix .env first. For a fresh setup run:  python -m scripts.init_env\n"
            "Then:  python -m scripts.seed_demo_data"
        )
        return 1

    try:
        import uagents  # noqa: F401
    except ImportError:
        print("uagents is not installed: python -m pip install -r requirements.txt")
        return 1

    children: list[tuple[str, subprocess.Popen]] = []

    def _shutdown(*_args) -> None:
        if not children:
            return
        print("\nShutting down...")
        for _, child in children:
            if child.poll() is None:
                child.terminate()
        for _, child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()

    def _stop_and_exit(code: int) -> int:
        _shutdown()
        return code

    signal.signal(signal.SIGINT, lambda *_: sys.exit(_stop_and_exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(_stop_and_exit(0)))

    for spec in _processes():
        env = os.environ.copy()
        env.update(spec.get("env", {}))
        if spec["module"] == "uvicorn":
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "rsvp_web.app:app_from_env",
                    "--factory",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    str(internal_rsvp_port),
                ],
                cwd=REPO_ROOT,
                env=env,
            )
        else:
            child = subprocess.Popen(
                [sys.executable, "-m", spec["module"]], cwd=REPO_ROOT, env=env
            )
        children.append((spec["name"], child))
        print(f"started {spec['name']} (pid {child.pid})")

    if single_port:
        gateway = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "scripts.public_gateway",
                "--port",
                str(platform_port),
            ],
            cwd=REPO_ROOT,
            env=env,
        )
        children.append(("gateway", gateway))
        print(f"started gateway (pid {gateway.pid}) on public port {platform_port}")

    print(
        "\nLocal stack running:"
        f"\n  coordinator : http://127.0.0.1:{DEFAULT_AGENT_PORTS['coordinator']}"
        f" (AGENT_MAILBOX={str(settings.agent_mailbox).lower()})"
        f"\n  supplier    : http://127.0.0.1:{DEFAULT_AGENT_PORTS['supplier']}"
        f"\n  outreach    : http://127.0.0.1:{DEFAULT_AGENT_PORTS['outreach']}"
        f"\n  rsvp        : http://127.0.0.1:{internal_rsvp_port}  (public base: {settings.rsvp_base_url})"
        "\nAgent HTTP endpoints exist for other agents; they are not a REST API."
        "\nOpening an agent port in a browser shows a status page at /; the RSVP URL above is the web UI."
        "\nKeep this process alive for ASI:One/Agentverse to reach the coordinator. Ctrl+C stops all."
    )

    # Monitor every child: if one dies, stop the rest instead of leaving a
    # half-running stack (this matters on a deployed host).
    try:
        while True:
            time.sleep(1)
            for name, child in children:
                code = child.poll()
                if code is not None:
                    if code != 0:
                        print(f"\n{name} exited with code {code}; stopping the stack.")
                        return _stop_and_exit(1)
                    print(f"{name} exited; stopping the stack.")
                    return _stop_and_exit(0)
    except KeyboardInterrupt:
        return _stop_and_exit(0)


if __name__ == "__main__":
    sys.exit(main())
