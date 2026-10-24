"""Outreach agent wiring: protocol handlers delegating to OutreachService."""

from __future__ import annotations

from shared.logging_config import get_logger
from shared.messages import (
    InvitationDraftRequest,
    RsvpCountRequest,
    RsvpSubmitRequest,
    SendInvitationRequest,
)

logger = get_logger("outreach.agent")


def build_protocol(service):
    from uagents import Protocol

    proto = Protocol(name="WorkshopPilotOutreach", version="1.0.0")

    @proto.on_message(InvitationDraftRequest, replies=None)
    async def on_draft_request(ctx, sender, msg):
        logger.info("invitation draft request %s (event %s)", msg.request_id, msg.event_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(SendInvitationRequest, replies=None)
    async def on_send_request(ctx, sender, msg):
        logger.info("send invitation request %s (invitation %s)", msg.request_id, msg.invitation_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(RsvpCountRequest, replies=None)
    async def on_count_request(ctx, sender, msg):
        logger.info("rsvp count request %s (event %s)", msg.request_id, msg.event_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(RsvpSubmitRequest, replies=None)
    async def on_rsvp_submit(ctx, sender, msg):
        logger.info("rsvp submit request %s (code supplied)", msg.request_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    return proto
