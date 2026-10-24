"""Supplier agent wiring: protocol handlers delegating to SupplierService.

Thin layer only — every business decision lives in the service. Message
contracts come from shared.messages (pydantic v1 / uAgents-compatible models).
"""

from __future__ import annotations

from shared.logging_config import get_logger
from shared.messages import (
    PaymentStatusRequest,
    QuoteRequest,
    ReservationRequest,
    ReservationSupersedeRequest,
)

logger = get_logger("supplier.agent")


def build_protocol(service):
    from uagents import Protocol

    proto = Protocol(name="WorkshopPilotSupplier", version="1.0.0")

    @proto.on_message(QuoteRequest, replies=None)
    async def on_quote_request(ctx, sender, msg):
        logger.info("quote request %s from %s (event %s)", msg.request_id, sender, msg.event_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(ReservationRequest, replies=None)
    async def on_reservation_request(ctx, sender, msg):
        logger.info("reservation request %s (event %s)", msg.request_id, msg.event_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(ReservationSupersedeRequest, replies=None)
    async def on_supersede_request(ctx, sender, msg):
        logger.info("supersede request %s (event %s)", msg.request_id, msg.event_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    @proto.on_message(PaymentStatusRequest, replies=None)
    async def on_payment_status_request(ctx, sender, msg):
        logger.info("payment status request %s (payment %s)", msg.request_id, msg.payment_id)
        result = service.handle(msg)
        for outbound in result.outgoing:
            await ctx.send(sender, outbound.msg)

    return proto
