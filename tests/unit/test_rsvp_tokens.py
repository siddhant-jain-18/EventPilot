"""RSVP token security and single-use semantics."""

from __future__ import annotations

import pytest

from rsvp_web.tokens import hash_code, issue_rsvp_token, verify_rsvp_token
from shared.errors import AuthorizationError, ExpiredError


def test_roundtrip():
    issued = issue_rsvp_token(
        "secret-1", event_id="E-1", recipient_ref="recipient-1", token_id="TK-1", ttl_seconds=3600
    )
    payload = verify_rsvp_token("secret-1", issued.token, event_id="E-1")
    assert payload["event_id"] == "E-1"
    assert payload["ref"] == "recipient-1"
    assert payload["tid"] == "TK-1"


def test_no_personal_data_in_token_url():
    issued = issue_rsvp_token(
        "secret-1",
        event_id="E-1",
        recipient_ref="recipient-1",
        token_id="TK-1",
        ttl_seconds=3600,
    )
    assert "alice" not in issued.token
    assert "@" not in issued.token


def test_expired_token_rejected():
    import time

    issued = issue_rsvp_token(
        "secret-1", event_id="E-1", recipient_ref="r", token_id="TK-1", ttl_seconds=1
    )
    time.sleep(1.1)
    with pytest.raises(ExpiredError):
        verify_rsvp_token("secret-1", issued.token)


def test_tampered_payload_rejected():
    issued = issue_rsvp_token(
        "secret-1", event_id="E-1", recipient_ref="r", token_id="TK-1", ttl_seconds=3600
    )
    body, mac = issued.token.split(".", 1)
    tampered = body[:-2] + "AA." + mac
    with pytest.raises((AuthorizationError, Exception)):
        verify_rsvp_token("secret-1", tampered)


def test_wrong_secret_rejected():
    issued = issue_rsvp_token(
        "secret-1", event_id="E-1", recipient_ref="r", token_id="TK-1", ttl_seconds=3600
    )
    with pytest.raises(AuthorizationError):
        verify_rsvp_token("secret-2", issued.token)


def test_event_mismatch_rejected():
    issued = issue_rsvp_token(
        "secret-1", event_id="E-1", recipient_ref="r", token_id="TK-1", ttl_seconds=3600
    )
    with pytest.raises(AuthorizationError):
        verify_rsvp_token("secret-1", issued.token, event_id="E-OTHER")


def test_code_hash_is_stable_and_upper():
    assert hash_code(" abc123 ") == hash_code("ABC123")
    assert hash_code("ABC123") != hash_code("ABC124")
