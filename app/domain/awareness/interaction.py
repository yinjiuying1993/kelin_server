"""Valid interaction classification. No IO."""

from __future__ import annotations

from app.domain.awareness.types import AwarenessFact

VALID_INTERACTION_TYPES = frozenset(
    {
        "dialogue.completed",
        "feed.food.accepted",
        "feed.knowledge.accepted",
        "feed.emotion.accepted",
        "promise.created",
        "promise.completed",
        "pact.answer.submitted",
        "pact.completed",
        "recall.completed",
    }
)
NOT_INTERACTION_TYPES = frozenset(
    {
        "bootstrap",
        "clock_tick",
        "promise.cancelled",
        "memory.deleted",
        "memory.sealed",
        "memory.cleared",
        "memory.corrected",
    }
)


def is_valid_interaction(fact: AwarenessFact) -> bool:
    if not fact.accepted or not fact.valid_interaction:
        return False
    if fact.event_type in NOT_INTERACTION_TYPES:
        return False
    if fact.event_type == "recall.completed":
        return fact.food_recall_success
    return fact.event_type in VALID_INTERACTION_TYPES
