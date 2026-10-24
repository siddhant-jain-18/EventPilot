"""Email allowlist enforcement and honest provider outcomes."""

from __future__ import annotations

import smtplib

import pytest

from integrations.email_client import (
    ConsoleEmailProvider,
    OutgoingEmail,
    SmtpEmailProvider,
    build_provider,
    mask_email,
)
from shared.config import Settings, load_settings


def _msg(ref="recipient-1"):
    return OutgoingEmail(
        recipient_ref=ref,
        recipient_email="alice@example.test",
        subject="Invitation",
        body="Hello,\nYou are invited.",
        idempotency_key="ACT-1",
    )


def test_console_provider_never_claims_real_delivery():
    report = ConsoleEmailProvider().send_batch([_msg()])
    assert len(report.outcomes) == 1
    outcome = report.outcomes[0]
    assert outcome.status == "logged_console"
    assert report.all_logged_console
    assert not report.any_failed and not report.any_unknown


def test_mask_email_hides_local_part():
    assert mask_email("jane.doe@example.com").startswith("j***@")
    assert "jane.doe" not in mask_email("jane.doe@example.com")


def test_build_provider_selection():
    settings = load_settings(EMAIL_PROVIDER="console")
    assert isinstance(build_provider(settings), ConsoleEmailProvider)


def test_smtp_unknown_outcome_is_marked_uncertain():
    """Connection to a closed local port cannot prove delivery or failure:
    the provider must report 'unknown', not 'sent'."""
    provider = SmtpEmailProvider(
        host="127.0.0.1", port=1, username="", password="", from_address="noreply@example.test"
    )
    report = provider.send_batch([_msg()])
    assert report.any_unknown
    assert report.outcomes[0].status == "unknown"


def test_smtp_refusal_maps_to_failed(monkeypatch):
    provider = SmtpEmailProvider(
        host="smtp.example.test", port=25, username="", password="", from_address="noreply@example.test"
    )

    class FakeServer:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, *a, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, message):
            raise smtplib.SMTPRecipientsRefused({"victim@example.test": (550, "nope")})

    monkeypatch.setattr(smtplib, "SMTP", FakeServer)
    report = provider.send_batch([_msg()])
    assert report.any_failed
    assert not report.any_unknown


def test_smtp_success_maps_to_sent(monkeypatch):
    provider = SmtpEmailProvider(
        host="smtp.example.test", port=25, username="", password="", from_address="noreply@example.test"
    )

    class FakeServer:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, *a, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, message):
            return {}

    monkeypatch.setattr(smtplib, "SMTP", FakeServer)
    report = provider.send_batch([_msg()])
    assert report.outcomes[0].status == "sent"
    assert report.outcomes[0].provider_message_id
