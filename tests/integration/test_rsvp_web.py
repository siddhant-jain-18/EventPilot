"""RSVP web service: token validation, duplicate protection, signed counts."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from fastapi.testclient import TestClient

from rsvp_web.app import create_app
from rsvp_web.tokens import hash_code, issue_rsvp_token
from shared.config import Settings
from storage.database import OUTREACH_DDL, connect, init_db
from storage.outreach_repository import OutreachRepository

from tests.conftest import make_settings


@pytest.fixture()
def client(tmp_path):
    settings = make_settings(tmp_path)
    conn = connect(settings.database_path("outreach.db"))
    init_db(conn, OUTREACH_DDL)
    repo = OutreachRepository(conn)
    app = create_app({"outreach": repo}, settings)
    return TestClient(app), repo, settings


def _seed_token(repo, settings, event_id="E-1", token_id="TK-1"):
    issued = issue_rsvp_token(
        settings.rsvp_signing_secret,
        event_id=event_id,
        recipient_ref="recipient-1",
        token_id=token_id,
        ttl_seconds=3600,
    )
    repo.save_token(
        {
            "token_id": issued.token_id,
            "event_id": event_id,
            "invitation_id": "I-1",
            "token_hash": hashlib.sha256(issued.token.encode()).hexdigest(),
            "rsvp_code": issued.rsvp_code,
            "code_hash": hash_code(issued.rsvp_code),
            "recipient_ref": "recipient-1",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "created_at": "2026-10-24T06:00:00+00:00",
        }
    )
    return issued


def test_health(client):
    http, repo, settings = client
    response = http.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_ready_checks_database(client):
    http, repo, settings = client
    response = http.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_full_rsvp_flow_and_duplicate_protection(client):
    http, repo, settings = client
    issued = _seed_token(repo, settings)
    page = http.get(f"/rsvp/{issued.token}")
    assert page.status_code == 200
    assert "Confirm my attendance" in page.text

    submit = http.post(f"/rsvp/{issued.token}", data={"attendee_reference": "Alice"})
    assert submit.status_code == 200
    assert "confirmed" in submit.text.lower()
    assert repo.confirmed_count("E-1") == 1

    # duplicate submission must NOT inflate the count
    again = http.post(f"/rsvp/{issued.token}", data={"attendee_reference": "Alice"})
    assert "already" in again.text.lower()
    assert repo.confirmed_count("E-1") == 1


def test_invalid_and_expired_tokens(client):
    http, repo, settings = client
    assert http.get("/rsvp/not-a-token").status_code == 404
    import time as _time

    expired = issue_rsvp_token(
        settings.rsvp_signing_secret, event_id="E-1", recipient_ref="r", token_id="TK-EX", ttl_seconds=1
    )
    _time.sleep(1.1)
    assert http.get(f"/rsvp/{expired.token}").status_code == 410
    forged = issue_rsvp_token(
        "other-secret", event_id="E-1", recipient_ref="r", token_id="TK-F", ttl_seconds=3600
    )
    assert http.get(f"/rsvp/{forged.token}").status_code in (403, 404)


def test_count_endpoint_requires_signature(client):
    http, repo, settings = client
    assert http.get("/internal/events/E-1/count").status_code == 401
    ts = str(int(time.time()))
    sig = hmac.new(settings.rsvp_signing_secret.encode(), f"{ts}:E-1".encode(), hashlib.sha256).hexdigest()
    response = http.get(
        "/internal/events/E-1/count",
        headers={"x-wp-timestamp": ts, "x-wp-signature": sig},
    )
    assert response.status_code == 200
    assert response.json() == {"event_id": "E-1", "confirmed_count": 0}
    stale_ts = str(int(time.time()) - 4000)
    stale_sig = hmac.new(
        settings.rsvp_signing_secret.encode(), f"{stale_ts}:E-1".encode(), hashlib.sha256
    ).hexdigest()
    assert (
        http.get(
            "/internal/events/E-1/count",
            headers={"x-wp-timestamp": stale_ts, "x-wp-signature": stale_sig},
        ).status_code
        == 401
    )
