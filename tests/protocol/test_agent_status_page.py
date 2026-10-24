"""Each agent port answers ``GET /`` with a status page instead of a bare 404."""

from __future__ import annotations

import asyncio

import pytest


def test_agent_ports_answer_a_status_page_on_root():
    pytest.importorskip("uagents")
    from uagents import Agent

    from shared.landing import add_status_page

    agent = Agent(
        name="landing_test",
        seed="test-only-landing-page-seed",
        port=8199,
        endpoint=None,
        mailbox=False,
        resolve=False,
    )
    add_status_page(
        agent, service="landing_test", hint="hint", links={"agent_info": "/agent_info"}
    )

    status = asyncio.run(agent.handle_rest("GET", "/", None))
    assert status is not None
    assert status.service == "landing_test"
    assert status.status == "ok"
    assert status.address == agent.address
    assert status.links == {"agent_info": "/agent_info"}
