"""Read-only status page for the agent transport ports.

uAgents only serves its envelope routes (``POST /submit``, ``GET /agent_info``)
and answers every other path with ``404 {"error": "not found"}``. Opening an
agent port in a browser therefore looks like a broken service. Registering a
``GET /`` endpoint makes the port explain itself instead.
"""

from __future__ import annotations

from typing import Dict

from uagents_core.models import Model


class AgentStatus(Model):
    service: str
    status: str = "ok"
    address: str
    transport: str = "uAgents envelope protocol (POST /submit)"
    hint: str = ""
    links: Dict[str, str] = {}


class HealthStatus(Model):
    status: str
    service: str


def add_status_page(
    agent,
    *,
    service: str,
    hint: str,
    links: Dict[str, str] | None = None,
) -> None:
    """Serve ``GET /`` on ``agent`` with a human-readable status payload."""

    @agent.on_rest_get("/", AgentStatus)
    async def status_page(ctx):  # noqa: ANN001, ARG001 - uAgents passes its Context
        return AgentStatus(
            service=service,
            address=agent.address,
            hint=hint,
            links=links or {},
        )

    @agent.on_rest_get("/health", HealthStatus)
    async def health_page(ctx):  # noqa: ANN001, ARG001 - uAgents passes its Context
        # Some PaaS configurations expose the agent listener by default. Keep
        # a real platform probe here as a safe fallback during rollout.
        return HealthStatus(status="ok", service=service)

    @agent.on_rest_get("/ready", HealthStatus)
    async def ready_page(ctx):  # noqa: ANN001, ARG001 - uAgents passes its Context
        return HealthStatus(status="ready", service=service)
