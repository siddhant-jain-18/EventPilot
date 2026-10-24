"""Seed the supplier's demo inventory (idempotent).

Run:  python -m scripts.seed_demo_data
"""

from __future__ import annotations

from shared.config import load_settings
from shared.inventory import DEMO_PACKAGES
from storage.database import SUPPLIER_DDL, connect, init_db
from storage.supplier_repository import SupplierRepository


def main() -> int:
    settings = load_settings()
    conn = connect(settings.database_path("supplier.db"))
    init_db(conn, SUPPLIER_DDL)
    repo = SupplierRepository(conn)
    for package in DEMO_PACKAGES:
        repo.seed_package(package.dict())
    print("Seeded demo inventory (FICTIONAL DEMO DATA):")
    for pkg in repo.list_packages():
        print(
            f"  {pkg['package_id']:20s} {pkg['name']:14s} capacity={pkg['capacity']:4d} "
            f"price={pkg['price_inr_paise'] / 100:>10,.0f} INR"
        )
    print("These are demo packages; no real supplier or venue stands behind them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
