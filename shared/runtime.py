"""Lightweight runtime envelope shared by the three agent services.

Services return :class:`TurnResult`: user-facing replies (text plus an
optional Interactive Card envelope) and outbound inter-agent messages. The
uAgents handler layer (agents/*/handlers.py) translates these into
``ctx.send`` calls; tests drive services directly through an in-memory bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Reply:
    text: str
    card: dict | None = None  # {"block": MetadataContent, "card_kind": str, ...}


@dataclass
class Outbound:
    role: str  # "supplier" | "outreach" | "coordinator"
    msg: Any


@dataclass
class TurnResult:
    replies: list[Reply] = field(default_factory=list)
    outgoing: list[Outbound] = field(default_factory=list)

    def add(self, text: str, card: dict | None = None) -> None:
        self.replies.append(Reply(text=text, card=card))

    def send(self, role: str, msg: Any) -> None:
        self.outgoing.append(Outbound(role=role, msg=msg))
