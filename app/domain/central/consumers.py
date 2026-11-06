"""Event consumer whitelist owned by central. Not user_actions."""

from __future__ import annotations

from app.domain.central.types import Consumer

EVENT_CONSUMERS: dict[str, tuple[Consumer, ...]] = {
    "pact.answer.submitted": ("awareness",),
    "pact.completed": ("memory",),
    "recall.completed": ("awareness", "memory"),
    "dialogue.completed": ("memory",),
    "visit.completed": ("awareness",),
    "memory.corrected": ("memory", "room"),
    "memory.sealed": ("memory", "room"),
    "memory.deleted": ("memory", "room"),
    "memory.cleared": ("memory", "room"),
}

CHAT_POST_PERSIST_CONSUMERS: tuple[Consumer, ...] = EVENT_CONSUMERS["dialogue.completed"]
BOOTSTRAP_CONSUMERS: tuple[Consumer, ...] = ("awareness", "room")
TICK_FORBIDDEN_CONSUMERS: frozenset[Consumer] = frozenset({"memory", "chat"})


def consumers_for_event(event_type: str) -> tuple[Consumer, ...]:
    try:
        return EVENT_CONSUMERS[event_type]
    except KeyError as exc:
        raise ValueError(f"unknown event type: {event_type}") from exc
