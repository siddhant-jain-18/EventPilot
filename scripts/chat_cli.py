"""Interactive local chat client for the coordinator agent.

Run this in a second terminal while scripts.run_local is running:

    python -m scripts.chat_cli
    python -m scripts.chat_cli agent1q...   # explicit coordinator address

Type a message and press Enter. Ctrl+C exits. The client has its own identity
and port (8123) and is only a demo driver — it is not part of the product.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.config import load_settings


def _coordinator_address(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1].strip():
        return argv[1].strip()
    env_value = os.environ.get("COORDINATOR_ADDRESS", "").strip()
    if env_value:
        return env_value
    address_file = Path("data/coordinator.address")
    if address_file.exists():
        value = address_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    return ""


def main() -> int:
    try:
        from uagents import Agent, Context
        from uagents_core.contrib.protocols.chat import (
            ChatAcknowledgement,
            ChatMessage,
            TextContent,
        )
    except ImportError:
        print("uagents is not installed: python -m pip install -r requirements.txt")
        return 1

    settings = load_settings()
    target = _coordinator_address(sys.argv)
    if not target:
        print(
            "No coordinator address found.\n"
            "Start scripts.run_local first (it writes data/coordinator.address), or pass "
            "the address: python -m scripts.chat_cli agent1q..."
        )
        return 1

    client = Agent(
        name="eventpilot_chat_cli",
        seed=settings.agent_seed or "EventPilot-chat-cli-local-demo-seed",
        port=8123,
        endpoint=["http://127.0.0.1:8123/submit"],
        resolve=False,
    )

    async def _read_loop(ctx: Context) -> None:
        loop = asyncio.get_running_loop()
        print(f"Chatting with {target}\nType a message and press Enter. Ctrl+C exits.\n")
        while True:
            try:
                text = await loop.run_in_executor(None, input, "you> ")
            except (EOFError, KeyboardInterrupt):
                return
            text = (text or "").strip()
            if not text:
                continue
            if text.lower() in ("quit", "exit"):
                print("Close the window or press Ctrl+C to stop the client.")
                continue
            await ctx.send(
                target,
                ChatMessage(
                    timestamp=datetime.now(timezone.utc),
                    content=[TextContent(text=text)],
                ),
            )

    @client.on_event("startup")
    async def start_chat(ctx: Context):
        ctx.logger.info("local chat client ready (address %s)", client.address)
        asyncio.create_task(_read_loop(ctx))

    @client.on_message(ChatMessage)
    async def on_message(ctx: Context, sender: str, msg: ChatMessage):
        for item in msg.content:
            if isinstance(item, TextContent):
                print(f"\nagent> {item.text}\n", flush=True)

    @client.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    try:
        client.run()
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
