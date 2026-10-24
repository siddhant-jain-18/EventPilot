"""Reset demo data (never configuration or secrets).

Run:  python -m scripts.reset_demo [--all | --owner <sender>]
- default: clears quotes/reservations/payments/tokens/rsvps/audit and the
  simulated chain, keeps seeds and the seeded inventory.
- --owner <sender>: coordinator-side, only that sender's events (used by the
  in-chat 'reset my demo' command).
"""

from __future__ import annotations

import argparse
import sys

from shared.config import load_settings
from storage.database import (
    COORDINATOR_DDL,
    OUTREACH_DDL,
    SUPPLIER_DDL,
    connect,
    init_db,
)
from storage.coordinator_repository import CoordinatorRepository
from storage.outreach_repository import OutreachRepository
from storage.supplier_repository import SupplierRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset WorkshopPilot demo data")
    parser.add_argument("--all", action="store_true", help="clear all demo databases")
    parser.add_argument("--owner", default="", help="only reset this sender's coordinator data")
    args = parser.parse_args()

    settings = load_settings()
    coord_conn = connect(settings.database_path("coordinator.db"))
    init_db(coord_conn, COORDINATOR_DDL)
    supplier_conn = connect(settings.database_path("supplier.db"))
    init_db(supplier_conn, SUPPLIER_DDL)
    outreach_conn = connect(settings.database_path("outreach.db"))
    init_db(outreach_conn, OUTREACH_DDL)

    if args.owner:
        removed = CoordinatorRepository(coord_conn).reset_owner(args.owner)
        print(f"Removed {removed} event(s) for owner {args.owner}.")
        return 0

    if not args.all:
        print("Default reset clears transactional demo state (inventory is kept).")
    SupplierRepository(supplier_conn).reset_demo()
    OutreachRepository(outreach_conn).reset_demo()
    coord_conn.executescript(
        """
        DELETE FROM events; DELETE FROM plans; DELETE FROM quotes; DELETE FROM approvals;
        DELETE FROM reservations; DELETE FROM payments; DELETE FROM invitations;
        DELETE FROM rsvp_counts; DELETE FROM spend_ledger; DELETE FROM outbound_requests;
        DELETE FROM audit_log;
        """
    )
    coord_conn.commit()
    chain = settings.database_path("simulated_chain.jsonl")
    if chain.exists():
        chain.unlink()
    print("Demo data cleared: coordinator, supplier, outreach, simulated chain.")
    print("Seeds, .env and the demo inventory were NOT touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
