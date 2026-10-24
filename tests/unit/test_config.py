"""Configuration rules that matter for local and hosted deployments."""

from __future__ import annotations

from shared.config import Settings, load_settings


def test_mailbox_and_public_endpoint_are_mutually_exclusive():
    settings = Settings(
        agent_mailbox=True, agent_endpoint="https://demo.example.com/submit"
    )
    problems = settings.validate()
    assert any("mutually exclusive" in problem for problem in problems)


def test_public_endpoint_must_be_a_submit_url():
    settings = Settings(agent_endpoint="https://demo.example.com")
    problems = settings.validate()
    assert any("/submit" in problem for problem in problems)
    assert Settings(agent_endpoint="https://demo.example.com/submit").validate() == []


def test_duplicate_seeds_are_rejected():
    settings = Settings(
        coordinator_seed="same-seed",
        supplier_seed="same-seed",
        outreach_seed="other-seed",
    )
    assert any("must be different" in problem for problem in settings.validate())


def test_agent_endpoints_follow_the_configured_mode():
    assert Settings(agent_mailbox=True).agent_endpoints(8101) is None
    assert Settings(agent_endpoint="https://demo.example.com/submit").agent_endpoints(
        8101
    ) == ["https://demo.example.com/submit"]
    assert Settings(agent_port=8101).agent_endpoints(8101) == [
        "http://127.0.0.1:8101/submit"
    ]
    assert Settings().agent_endpoints(8103) == ["http://127.0.0.1:8103/submit"]


def test_rsvp_port_falls_back_to_platform_port(monkeypatch):
    monkeypatch.setenv("RSVP_PORT", "")
    monkeypatch.setenv("PORT", "10000")
    assert load_settings().rsvp_port == 10000
    monkeypatch.setenv("RSVP_PORT", "9104")
    assert load_settings().rsvp_port == 9104


def test_production_secret_must_be_long_enough():
    settings = Settings(app_env="production", rsvp_signing_secret="too-short")
    assert any("at least 32" in problem for problem in settings.validate())


def test_invalid_runtime_values_are_rejected():
    settings = Settings(app_env="production", rsvp_port=0, rate_limit_per_minute=0)
    problems = settings.validate()
    assert any("between 1 and 65535" in problem for problem in problems)
    assert any("must be positive" in problem for problem in problems)
