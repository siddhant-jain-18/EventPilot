"""SQLite persistence: connections, transactions and schema DDL.

Each agent owns its own database file (spec section 4.4/13). Connections use
WAL mode and a busy timeout so the RSVP web service can share the outreach
database safely across processes. All timestamps are UTC ISO strings.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from shared.time_utils import now_iso


def connect(path: str | Path) -> sqlite3.Connection:
    # check_same_thread=False: the RSVP service (FastAPI/TestClient) touches the
    # shared outreach database from worker threads; WAL + busy_timeout protect it.
    conn = sqlite3.connect(str(path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10].upper()}"


def init_db(conn: sqlite3.Connection, ddl: str) -> None:
    with transaction(conn):
        conn.executescript(ddl)


# ---------------------------------------------------------------------------
# Coordinator database
# ---------------------------------------------------------------------------

COORDINATOR_DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    owner_sender TEXT NOT NULL,
    title TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'workshop',
    date TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
    city TEXT NOT NULL,
    expected_attendees INTEGER NOT NULL,
    budget_inr_paise INTEGER NOT NULL,
    equipment_requirements TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    current_plan_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    attendee_count INTEGER NOT NULL,
    options_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    PRIMARY KEY (event_id, plan_version)
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    supplier_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    package_name TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    attendee_count INTEGER NOT NULL,
    price_inr_paise INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    deposit_fet_base INTEGER NOT NULL,
    inclusions TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    amendment_of_reservation_id TEXT
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL,
    owner_sender TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    action_payload_hash TEXT NOT NULL,
    amount_inr_paise INTEGER,
    recipient_set_hash TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_event ON approvals(event_id, status);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    quote_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_mode TEXT,
    action_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    expected_amount_fet_base INTEGER NOT NULL,
    asset TEXT NOT NULL DEFAULT 'atestfet',
    network TEXT NOT NULL DEFAULT 'simulated',
    recipient_address TEXT NOT NULL DEFAULT '',
    transaction_id TEXT,
    verification_status TEXT NOT NULL DEFAULT 'not_started',
    payment_mode TEXT NOT NULL DEFAULT 'simulated',
    amount_inr_paise INTEGER,
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS invitations (
    invitation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    draft_version INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body_preview TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL,
    recipient_set_hash TEXT NOT NULL,
    recipient_count INTEGER NOT NULL,
    approval_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    provider_message_ids TEXT NOT NULL DEFAULT '[]',
    mode TEXT NOT NULL DEFAULT 'console',
    created_at TEXT NOT NULL,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS rsvp_counts (
    event_id TEXT PRIMARY KEY,
    confirmed_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS spend_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    amount_inr_paise INTEGER NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbound_requests (
    request_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    owner_sender TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    safe_metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event_id, timestamp);
"""

# ---------------------------------------------------------------------------
# Supplier database
# ---------------------------------------------------------------------------

SUPPLIER_DDL = """
CREATE TABLE IF NOT EXISTS packages (
    package_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    price_inr_paise INTEGER NOT NULL,
    equipment TEXT NOT NULL DEFAULT '[]',
    inclusions TEXT NOT NULL DEFAULT '[]',
    availability_status TEXT NOT NULL DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS quotes (
    quote_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    package_id TEXT NOT NULL,
    attendee_count INTEGER NOT NULL,
    price_inr_paise INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'INR',
    deposit_fet_base INTEGER NOT NULL,
    inclusions TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    amendment_of_reservation_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_supplier_quotes_event ON quotes(event_id, status);

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    quote_id TEXT NOT NULL,
    package_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_mode TEXT,
    action_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_supplier_resv_quote ON reservations(quote_id);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    reservation_id TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    expected_amount_fet_base INTEGER NOT NULL,
    asset TEXT NOT NULL DEFAULT 'atestfet',
    network TEXT NOT NULL DEFAULT 'simulated',
    recipient_address TEXT NOT NULL DEFAULT '',
    transaction_id TEXT,
    verification_status TEXT NOT NULL DEFAULT 'not_started',
    payment_mode TEXT NOT NULL DEFAULT 'simulated',
    created_at TEXT NOT NULL,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS txn_replay (
    tx_id TEXT PRIMARY KEY,
    used_for TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    safe_metadata TEXT NOT NULL DEFAULT '{}'
);
"""

# ---------------------------------------------------------------------------
# Outreach database
# ---------------------------------------------------------------------------

OUTREACH_DDL = """
CREATE TABLE IF NOT EXISTS invitations (
    invitation_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    draft_version INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    recipient_refs TEXT NOT NULL DEFAULT '[]',
    recipient_emails TEXT NOT NULL DEFAULT '[]',
    recipient_count INTEGER NOT NULL,
    recipient_set_hash TEXT NOT NULL,
    approval_id TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    provider_message_ids TEXT NOT NULL DEFAULT '[]',
    idempotency_key TEXT UNIQUE,
    mode TEXT NOT NULL DEFAULT 'console',
    created_at TEXT NOT NULL,
    sent_at TEXT,
    failure_reason TEXT
);

CREATE TABLE IF NOT EXISTS rsvp_tokens (
    token_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    invitation_id TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    rsvp_code TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    recipient_ref TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_event ON rsvp_tokens(event_id);

CREATE TABLE IF NOT EXISTS rsvps (
    rsvp_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    attendee_reference TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (event_id, token_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    audit_id TEXT PRIMARY KEY,
    event_id TEXT,
    correlation_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    safe_metadata TEXT NOT NULL DEFAULT '{}'
);
"""
