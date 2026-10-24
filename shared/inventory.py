"""Single source of truth for the demonstration inventory.

The supplier seeds its SQLite inventory from this module and the coordinator
validates supplier responses against the same immutable catalog (Plan.md
section 5.3: "The server-side quote record is the only authority on price,
capacity, recipient and expiry").

These are FICTIONAL DEMO PACKAGES. The UI, README and chat narration must
label them as demo data. There is no real venue or supplier behind them.

Money rules:
- INR amounts are integer paise (Rs 14,500 = 1_450_000 paise).
- The symbolic testnet reservation deposit is an integer amount of atto-FET
  (1 tFET = 10**18 atestfet). It is NOT an INR equivalent and there is no
  exchange rate between the two.
"""

from __future__ import annotations

from shared.models import SupplierPackage

FET_DECIMALS = 18
ATESTFET = 10**FET_DECIMALS

PACKAGE_DEPOSITS_ATFET: dict[str, int] = {
    "wp-starter-lab": 100_000_000_000_000_000,  # 0.10 tFET
    "wp-standard-lab": 130_000_000_000_000_000,  # 0.13 tFET
    "wp-premium-lab": 180_000_000_000_000_000,  # 0.18 tFET
}


def _pkg(
    package_id: str,
    name: str,
    capacity: int,
    price_inr_paise: int,
    equipment: list[str],
    inclusions: list[str],
) -> SupplierPackage:
    return SupplierPackage(
        package_id=package_id,
        name=name,
        capacity=capacity,
        price_inr_paise=price_inr_paise,
        equipment=equipment,
        inclusions=inclusions,
        availability_status="available",
    )


DEMO_PACKAGES: list[SupplierPackage] = [
    _pkg(
        "wp-starter-lab",
        "Starter Lab",
        60,
        1_450_000,
        ["projector", "wifi", "seating"],
        ["AV desk", "basic seating for 60", "power strips"],
    ),
    _pkg(
        "wp-standard-lab",
        "Standard Lab",
        80,
        1_920_000,
        ["projector", "wifi", "seating"],
        ["AV desk", "seating for 80", "whiteboard", "refreshment counter"],
    ),
    _pkg(
        "wp-premium-lab",
        "Premium Lab",
        100,
        2_600_000,
        ["projector", "wifi", "seating", "recording_setup"],
        [
            "AV desk",
            "seating for 100",
            "recording setup",
            "refreshment counter",
            "dedicated support staff",
        ],
    ),
]

DEMO_CATALOG: dict[str, SupplierPackage] = {p.package_id: p for p in DEMO_PACKAGES}
SUPPLIER_ID = "workshoppilot-supplier-demo"
