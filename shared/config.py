"""Environment-driven configuration with fail-fast validation.

Every integration loads credentials from environment variables only.
``.env.example`` documents every variable; production mode (APP_ENV=production)
refuses to start when a mandatory setting is missing. Secrets never enter the
repository or the logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Ports used by scripts/run_local.py and scripts/health_check.py.
DEFAULT_AGENT_PORTS = {"coordinator": 8101, "supplier": 8102, "outreach": 8103}
DEFAULT_RSVP_PORT = 8104


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    app_env: str = "development"
    database_dir: str = "./data"
    log_level: str = "INFO"

    # Agents
    agent_name: str = ""
    agent_seed: str = ""
    agent_port: int = 0  # per-agent default is applied by the agent module
    agent_mailbox: bool = False
    # Optional public endpoint advertised to other agents (for example
    # https://my-host.example.com/submit). Mutually exclusive with mailbox
    # mode: when AGENT_MAILBOX=true the Agentverse relay is the endpoint.
    agent_endpoint: str = ""

    # Peer addresses (coordinator -> peers)
    coordinator_seed: str = ""
    supplier_seed: str = ""
    outreach_seed: str = ""
    supplier_address: str = ""
    outreach_address: str = ""

    # ASI1 LLM
    asi1_api_key: str = ""
    asi1_base_url: str = "https://api.asi1.ai/v1"
    asi1_model: str = "asi1-mini"
    asi1_timeout_seconds: int = 8

    # Payments
    payment_mode: str = "simulated"
    payment_network: str = "simulated"
    payment_rpc_url: str = ""
    supplier_wallet_address: str = "wp-demo-supplier-wallet"
    coordinator_wallet_address: str = "wp-demo-coordinator-wallet"
    simulated_coordinator_balance_fet: str = "1.0"
    max_transfer_fet: str = "0.2"
    per_event_max_fet: str = "0.6"

    # Email
    email_provider: str = "console"
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_from: str = ""
    allowed_test_recipients: list[str] = field(default_factory=list)

    # RSVP
    rsvp_base_url: str = "http://localhost:8104"
    rsvp_signing_secret: str = ""
    rsvp_token_ttl_seconds: int = 14 * 24 * 3600

    # Behaviour tuning (short values are for tests only)
    quote_ttl_seconds: int = 30 * 60
    approval_ttl_seconds: int = 15 * 60
    agent_reply_timeout_seconds: float = 30.0
    rate_limit_per_minute: int = 30

    # Ports
    rsvp_port: int = DEFAULT_RSVP_PORT

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def llm_enabled(self) -> bool:
        return bool(self.asi1_api_key)

    def database_path(self, name: str) -> Path:
        base = Path(self.database_dir)
        base.mkdir(parents=True, exist_ok=True)
        return base / name

    def agent_endpoints(self, default_port: int) -> list[str] | None:
        """Endpoints to advertise for this agent process.

        - ``AGENT_ENDPOINT`` (public URL) wins for hosted deployments.
        - In mailbox mode no endpoint is passed, otherwise uAgents would
          override the Agentverse relay with the local/LAN address.
        - Otherwise advertise the local ``/submit`` endpoint used by the
          three-process demo.
        """
        if self.agent_endpoint:
            return [self.agent_endpoint]
        if self.agent_mailbox:
            return None
        return [f"http://127.0.0.1:{self.agent_port or default_port}/submit"]

    def validate(self) -> list[str]:
        """Return the list of problems; production also fails on secrets."""
        problems: list[str] = []
        if self.is_production:
            for name, value in (
                ("RSVP_SIGNING_SECRET", self.rsvp_signing_secret),
                ("COORDINATOR_SEED", self.coordinator_seed),
                ("SUPPLIER_SEED", self.supplier_seed),
                ("OUTREACH_SEED", self.outreach_seed),
            ):
                if not value:
                    problems.append(f"missing mandatory production setting: {name}")
            if self.payment_mode == "testnet" and not self.payment_rpc_url:
                problems.append("PAYMENT_MODE=testnet requires PAYMENT_RPC_URL")
        if self.payment_mode not in ("simulated", "testnet"):
            problems.append("PAYMENT_MODE must be 'simulated' or 'testnet'")
        if self.email_provider not in ("console", "smtp"):
            problems.append("EMAIL_PROVIDER must be 'console' or 'smtp'")
        if self.email_provider == "smtp" and not self.email_smtp_host:
            problems.append("EMAIL_PROVIDER=smtp requires EMAIL_SMTP_HOST")
        if not self.allowed_test_recipients and self.email_provider == "smtp":
            problems.append("EMAIL_PROVIDER=smtp requires ALLOWED_TEST_RECIPIENTS")
        if self.agent_mailbox and self.agent_endpoint:
            problems.append(
                "AGENT_MAILBOX=true and AGENT_ENDPOINT are mutually exclusive; "
                "unset AGENT_ENDPOINT when using the Agentverse mailbox relay"
            )
        if self.agent_endpoint and not self.agent_endpoint.rstrip("/").endswith("/submit"):
            problems.append("AGENT_ENDPOINT must end with /submit (it is the uAgents envelope endpoint)")
        seeds = [
            seed
            for seed in (self.coordinator_seed, self.supplier_seed, self.outreach_seed)
            if seed
        ]
        if len(seeds) != len(set(seeds)):
            problems.append(
                "COORDINATOR_SEED, SUPPLIER_SEED and OUTREACH_SEED must be different "
                "(identical seeds produce identical agent addresses)"
            )
        return problems


def load_settings(**overrides: object) -> Settings:
    load_dotenv()
    settings = Settings(
        app_env=_env("APP_ENV", "development"),
        database_dir=_env("DATABASE_DIR", "./data"),
        log_level=_env("LOG_LEVEL", "INFO"),
        agent_name=_env("AGENT_NAME"),
        agent_seed=_env("AGENT_SEED"),
        agent_port=_env_int("AGENT_PORT", 0),
        agent_mailbox=_env_bool("AGENT_MAILBOX", False),
        agent_endpoint=_env("AGENT_ENDPOINT"),
        coordinator_seed=_env("COORDINATOR_SEED"),
        supplier_seed=_env("SUPPLIER_SEED"),
        outreach_seed=_env("OUTREACH_SEED"),
        supplier_address=_env("SUPPLIER_ADDRESS"),
        outreach_address=_env("OUTREACH_ADDRESS"),
        asi1_api_key=_env("ASI1_API_KEY"),
        asi1_base_url=_env("ASI1_BASE_URL", "https://api.asi1.ai/v1"),
        asi1_model=_env("ASI1_MODEL", "asi1-mini"),
        asi1_timeout_seconds=_env_int("ASI1_TIMEOUT_SECONDS", 8),
        payment_mode=_env("PAYMENT_MODE", "simulated"),
        payment_network=_env("PAYMENT_NETWORK", "simulated"),
        payment_rpc_url=_env("PAYMENT_RPC_URL"),
        supplier_wallet_address=_env("SUPPLIER_WALLET_ADDRESS", "wp-demo-supplier-wallet"),
        coordinator_wallet_address=_env("COORDINATOR_WALLET_ADDRESS", "wp-demo-coordinator-wallet"),
        simulated_coordinator_balance_fet=_env("SIMULATED_COORDINATOR_BALANCE_FET", "1.0"),
        max_transfer_fet=_env("MAX_TRANSFER_FET", "0.2"),
        per_event_max_fet=_env("PER_EVENT_MAX_FET", "0.6"),
        email_provider=_env("EMAIL_PROVIDER", "console"),
        email_smtp_host=_env("EMAIL_SMTP_HOST"),
        email_smtp_port=_env_int("EMAIL_SMTP_PORT", 587),
        email_smtp_user=_env("EMAIL_SMTP_USER"),
        email_smtp_password=_env("EMAIL_SMTP_PASSWORD"),
        email_from=_env("EMAIL_FROM"),
        allowed_test_recipients=[
            a.strip() for a in _env("ALLOWED_TEST_RECIPIENTS").split(",") if a.strip()
        ],
        rsvp_base_url=_env("RSVP_BASE_URL", "http://localhost:8104"),
        rsvp_signing_secret=_env("RSVP_SIGNING_SECRET"),
        rsvp_token_ttl_seconds=_env_int("RSVP_TOKEN_TTL_SECONDS", 14 * 24 * 3600),
        quote_ttl_seconds=_env_int("QUOTE_TTL_SECONDS", 30 * 60),
        approval_ttl_seconds=_env_int("APPROVAL_TTL_SECONDS", 15 * 60),
        agent_reply_timeout_seconds=float(
            _env("AGENT_REPLY_TIMEOUT_SECONDS", "30") or 30
        ),
        rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 30),
        # Cloud platforms inject PORT; RSVP_PORT takes precedence when set.
        rsvp_port=_env_int("RSVP_PORT", _env_int("PORT", DEFAULT_RSVP_PORT)),
    )
    for key, value in overrides.items():
        if value is not None and hasattr(settings, key):
            setattr(settings, key, value)
    return settings
