"""Coordinator agent wiring: ASI:One chat protocol + inter-agent handlers.

The chat protocol comes from the installed SDK
(uagents_core.contrib.protocols.chat) exactly as the official examples use
it. Interactive Cards ride in MetadataContent blocks built by the OFFICIAL
cards helpers; plain text narration always accompanies a card so the workflow
survives if a card fails to render.
"""

from __future__ import annotations

from uuid import uuid4

from shared.logging_config import get_logger
from shared.messages import (
    InvitationDraftResponse,
    PaymentStatusResponse,
    QuoteResponse,
    ReservationResponse,
    ReservationSupersedeResponse,
    RsvpCountResponse,
    RsvpSubmitResponse,
    SendInvitationResponse,
)

logger = get_logger("coordinator.agent")


def build_chat_protocol(service):
    """ACP chat protocol with card support."""
    from datetime import datetime, timezone

    from uagents import Protocol
    from uagents_core.contrib.protocols.chat import (
        ChatAcknowledgement,
        ChatMessage,
        EndSessionContent,
        MetadataContent,
        StartSessionContent,
        TextContent,
        chat_protocol_spec,
    )
    from uagents_core.contrib.protocols.chat.cards import (
        extract_card_response,
    )

    proto = Protocol(name=chat_protocol_spec.name, version=chat_protocol_spec.version)

    def _now():
        return datetime.now(timezone.utc)

    async def _send_text(ctx, sender, text: str, card: dict | None = None):
        content: list = [TextContent(text)]
        if card is not None and isinstance(card, dict):
            block = card.get("block")
            if block is not None and isinstance(block, MetadataContent):
                content.append(block)
        await ctx.send(sender, ChatMessage(content=content))

    @proto.on_message(ChatMessage, replies=None)
    async def on_chat_message(ctx, sender, msg):
        await ctx.send(
            sender,
            ChatAcknowledgement(acknowledged_msg_id=msg.msg_id, timestamp=_now()),
        )
        service._last_chat_sender = str(sender)  # noqa: SLF001 - reply target for async peer replies
        started = any(isinstance(c, StartSessionContent) for c in msg.content)
        if started:
            result = service.handle_user_text(str(sender), "")
            for reply in result.replies:
                await _send_text(ctx, sender, reply.text)
            return
        if any(isinstance(c, EndSessionContent) for c in msg.content):
            return

        # 1) structured card responses (MetadataContent blocks)
        for block in msg.content:
            if isinstance(block, MetadataContent):
                response = extract_card_response(block)
                if response is not None:
                    result = service.handle_card_response(str(sender), block.metadata)
                    for reply in result.replies:
                        await _send_text(ctx, sender, reply.text, reply.card)
                    await _forward(result, ctx)
                    return

        # 2) plain text
        text = msg.text()
        result = service.handle_user_text(str(sender), text)
        for reply in result.replies:
            await _send_text(ctx, sender, reply.text, reply.card)
        await _forward(result, ctx)

    async def _forward(result, ctx):
        for outbound in result.outgoing:
            if outbound.role == "supplier":
                destination = service.settings.supplier_address
            elif outbound.role == "outreach":
                destination = service.settings.outreach_address
            else:
                logger.warning("unexpected outbound role %s", outbound.role)
                continue
            if not destination:
                logger.warning(
                    "no destination configured for %s; request %s stays pending and will "
                    "time out honestly",
                    outbound.role,
                    getattr(outbound.msg, "request_id", "?"),
                )
                continue
            await ctx.send(destination, outbound.msg)

    return proto


def build_agent_protocol(service):
    """Inter-agent protocol for responses coming back from peers."""
    from uagents import Protocol

    proto = Protocol(name="WorkshopPilotCoordinator", version="1.0.0")

    @proto.on_message(QuoteResponse, replies=None)
    async def on_quote_response(ctx, sender, msg):
        logger.info("quote response %s (event %s)", msg.request_id, msg.event_id)
        result = service.handle_supplier_message(msg)
        await _emit(result, ctx)

    @proto.on_message(ReservationResponse, replies=None)
    async def on_reservation_response(ctx, sender, msg):
        logger.info("reservation response %s", msg.request_id)
        result = service.handle_supplier_message(msg)
        await _emit(result, ctx)

    @proto.on_message(ReservationSupersedeResponse, replies=None)
    async def on_supersede_response(ctx, sender, msg):
        result = service.handle_supplier_message(msg)
        await _emit(result, ctx)

    @proto.on_message(PaymentStatusResponse, replies=None)
    async def on_payment_response(ctx, sender, msg):
        logger.info("payment status response %s -> %s", msg.request_id, msg.verification_status)
        result = service.handle_supplier_message(msg)
        await _emit(result, ctx)

    @proto.on_message(InvitationDraftResponse, replies=None)
    async def on_draft_response(ctx, sender, msg):
        logger.info("invitation draft response %s", msg.request_id)
        result = service.handle_outreach_message(msg)
        await _emit(result, ctx)

    @proto.on_message(SendInvitationResponse, replies=None)
    async def on_send_response(ctx, sender, msg):
        logger.info("send invitation response %s -> %s", msg.request_id, msg.invitation_status)
        result = service.handle_outreach_message(msg)
        await _emit(result, ctx)

    @proto.on_message(RsvpCountResponse, replies=None)
    async def on_count_response(ctx, sender, msg):
        result = service.handle_outreach_message(msg)
        await _emit(result, ctx)

    @proto.on_message(RsvpSubmitResponse, replies=None)
    async def on_rsvp_submit_response(ctx, sender, msg):
        result = service.handle_outreach_message(msg)
        await _emit(result, ctx)

    async def _emit(result, ctx):
        """Send user-facing chat replies to the latest chat sender and forward
        outbound inter-agent messages."""
        from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent

        sender = getattr(service, "_last_chat_sender", None)
        for reply in result.replies:
            if sender:
                content: list = [TextContent(reply.text)]
                if reply.card and isinstance(reply.card, dict):
                    block = reply.card.get("block")
                    if block is not None and isinstance(block, MetadataContent):
                        content.append(block)
                await ctx.send(sender, ChatMessage(content=content))
        for outbound in result.outgoing:
            if outbound.role == "supplier":
                destination = service.settings.supplier_address
            elif outbound.role == "outreach":
                destination = service.settings.outreach_address
            else:
                continue
            if destination:
                await ctx.send(destination, outbound.msg)

    return proto
