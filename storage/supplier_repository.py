"""Supplier-side repositories: packages, quotes, reservations, payments and
transaction replay protection.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional

from shared.errors import NotFoundError
from shared.time_utils import now_iso
from storage.database import new_id


def _j(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _jl(raw: str) -> list:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


class SupplierRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # -- packages ----------------------------------------------------------

    def seed_package(self, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO packages (package_id, name, capacity, price_inr_paise,"
            " equipment, inclusions, availability_status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                p["package_id"], p["name"], p["capacity"], p["price_inr_paise"],
                _j(p.get("equipment") or []), _j(p.get("inclusions") or []),
                p.get("availability_status", "available"),
            ),
        )
        self.conn.commit()

    def list_packages(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM packages ORDER BY price_inr_paise").fetchall()
        return [self._package(r) for r in rows]

    def get_package(self, package_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM packages WHERE package_id = ?", (package_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"package {package_id} not in supplier inventory")
        return self._package(row)

    def set_availability(self, package_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE packages SET availability_status = ? WHERE package_id = ?",
            (status, package_id),
        )
        self.conn.commit()

    @staticmethod
    def _package(row: sqlite3.Row) -> dict:
        data = dict(row)
        data["equipment"] = _jl(data["equipment"])
        data["inclusions"] = _jl(data["inclusions"])
        return data

    # -- quotes ---------------------------------------------------------------

    def save_quote(self, q: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO quotes (quote_id, event_id, plan_version, package_id,"
            " attendee_count, price_inr_paise, currency, deposit_fet_base, inclusions, status,"
            " created_at, expires_at, amendment_of_reservation_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                q["quote_id"], q["event_id"], q["plan_version"], q["package_id"],
                q["attendee_count"], q["price_inr_paise"], q.get("currency", "INR"),
                q["deposit_fet_base"], _j(q.get("inclusions") or []), q.get("status", "active"),
                q["created_at"], q["expires_at"], q.get("amendment_of_reservation_id"),
            ),
        )
        self.conn.commit()

    def get_quote(self, quote_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM quotes WHERE quote_id = ?", (quote_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"quote {quote_id} not found")
        data = dict(row)
        data["inclusions"] = _jl(data["inclusions"])
        return data

    def expire_quotes(self, now: str) -> int:
        cur = self.conn.execute(
            "UPDATE quotes SET status = 'expired' WHERE status = 'active' AND expires_at <= ?",
            (now,),
        )
        self.conn.commit()
        return cur.rowcount

    def set_quote_status(self, quote_id: str, status: str) -> None:
        self.conn.execute("UPDATE quotes SET status = ? WHERE quote_id = ?", (status, quote_id))
        self.conn.commit()

    # -- reservations ---------------------------------------------------------------

    def save_reservation(self, r: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reservations (reservation_id, event_id, plan_version, quote_id,"
            " package_id, status, payment_mode, action_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                r["reservation_id"], r["event_id"], r["plan_version"], r["quote_id"],
                r["package_id"], r["status"], r.get("payment_mode"), r["action_id"],
                r["created_at"], r["updated_at"],
            ),
        )
        self.conn.commit()

    def get_reservation_by_action(self, action_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE action_id = ?", (action_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_reservation(self, reservation_id: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE reservation_id = ?", (reservation_id,)
        ).fetchone()
        if not row:
            raise NotFoundError(f"reservation {reservation_id} not found")
        return dict(row)

    def update_reservation(self, reservation_id: str, **fields: object) -> None:
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE reservations SET {sets} WHERE reservation_id = ?",
            (*fields.values(), reservation_id),
        )
        self.conn.commit()

    def active_reservation_for_event(self, event_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM reservations WHERE event_id = ? AND status IN"
            " ('reserved', 'payment_pending', 'paid', 'confirmed') ORDER BY created_at DESC LIMIT 1",
            (event_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- payments -----------------------------------------------------------------

    def save_payment(self, p: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO payments (payment_id, reservation_id, quote_id, event_id,"
            " expected_amount_fet_base, asset, network, recipient_address, transaction_id,"
            " verification_status, payment_mode, created_at, verified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                p["payment_id"], p["reservation_id"], p["quote_id"], p["event_id"],
                p["expected_amount_fet_base"], p.get("asset", "atestfet"), p.get("network", "simulated"),
                p.get("recipient_address", ""), p.get("transaction_id"),
                p.get("verification_status", "not_started"), p.get("payment_mode", "simulated"),
                p["created_at"], p.get("verified_at"),
            ),
        )
        self.conn.commit()

    def get_payment(self, payment_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_payment(self, payment_id: str, **fields: object) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE payments SET {sets} WHERE payment_id = ?", (*fields.values(), payment_id)
        )
        self.conn.commit()

    # -- replay protection -----------------------------------------------------------

    def consume_tx_id(self, tx_id: str, used_for: str) -> bool:
        """Atomically claim a transaction id; False means it was already used."""
        try:
            self.conn.execute(
                "INSERT INTO txn_replay (tx_id, used_for, created_at) VALUES (?, ?, ?)",
                (tx_id, used_for, now_iso()),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def audit(self, action: str, entity_type: str, entity_id: str, result: str, event_id: str | None = None, metadata: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (audit_id, event_id, actor, action, entity_type, entity_id, result, timestamp, safe_metadata)"
            " VALUES (?, ?, 'supplier', ?, ?, ?, ?, ?, ?)",
            (new_id("AU"), event_id, action, entity_type, entity_id, result, now_iso(), _j(metadata or {})),
        )
        self.conn.commit()

    # -- reset -------------------------------------------------------------

    def reset_demo(self) -> None:
        for table in ("quotes", "reservations", "payments", "txn_replay", "audit_log"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
