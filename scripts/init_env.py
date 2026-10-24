"""Create a ready-to-run .env from .env.example (works on every OS).

Run:  python -m scripts.init_env [--force]

Generates three distinct agent seeds, an RSVP signing secret and the two peer
addresses derived from those seeds, then writes them into .env. Existing .env
files are never overwritten unless --force is passed. Secrets are written to
the file only; they are not printed.
"""

from __future__ import annotations

import argparse
import re
import secrets
import sys
from pathlib import Path

from shared.config import load_settings

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / ".env.example"
TARGET = REPO_ROOT / ".env"


def _replace_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return text.rstrip("\n") + f"\n{key}={value}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a EventPilot .env")
    parser.add_argument(
        "--force", action="store_true", help="overwrite an existing .env (rotates secrets)"
    )
    args = parser.parse_args()

    if TARGET.exists() and not args.force:
        print(
            f"{TARGET.name} already exists; refusing to overwrite it.\n"
            "Use --force only if you intend to rotate seeds and addresses "
            "(this changes the agent addresses and requires re-registering them)."
        )
        return 1
    if not TEMPLATE.exists():
        print(f"Missing {TEMPLATE.name}; run this from the repository root.")
        return 1

    try:
        from uagents import Agent
    except ImportError:
        print("uagents is not installed: python -m pip install -r requirements.txt")
        return 1

    seeds = {
        "COORDINATOR_SEED": secrets.token_hex(24),
        "SUPPLIER_SEED": secrets.token_hex(24),
        "OUTREACH_SEED": secrets.token_hex(24),
        "RSVP_SIGNING_SECRET": secrets.token_hex(32),
    }
    addresses = {
        role: Agent(
            name=f"eventpilot_{role.lower()}", seed=seeds[f"{role}_SEED"], resolve=False
        ).address
        for role in ("COORDINATOR", "SUPPLIER", "OUTREACH")
    }

    text = TEMPLATE.read_text(encoding="utf-8")
    # The template marker is documentation only; a real .env must not include it.
    text = re.sub(r"^\[TEMPLATE\]\s*\n", "", text, count=1, flags=re.MULTILINE)
    for key, value in seeds.items():
        text = _replace_value(text, key, value)
    text = _replace_value(text, "SUPPLIER_ADDRESS", addresses["SUPPLIER"])
    text = _replace_value(text, "OUTREACH_ADDRESS", addresses["OUTREACH"])

    TARGET.write_text(text, encoding="utf-8")
    print("Wrote .env with fresh secrets and peer addresses.")
    for role in ("COORDINATOR", "SUPPLIER", "OUTREACH"):
        print(f"  {role} address: {addresses[role]}")
    print(
        "\nNext steps:\n"
        "  1. python -m scripts.seed_demo_data\n"
        "  2. python -m scripts.run_local        (keep it running)\n"
        "  3. python -m scripts.health_check     (second terminal)\n"
        "\nNever commit .env. Changing a seed changes its agent address."
    )
    # Confirm the file parses as expected without printing secrets.
    settings = load_settings()
    problems = settings.validate()
    if problems:
        print("\nConfiguration problems:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
