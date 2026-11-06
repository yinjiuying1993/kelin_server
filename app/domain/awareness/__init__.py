"""Awareness time and life-state pure functions. No IO."""

from app.domain.awareness.interaction import VALID_INTERACTION_TYPES, is_valid_interaction
from app.domain.awareness.life import decay_life, life_band
from app.domain.awareness.lost_recall import food_recall_returns_home
from app.domain.awareness.resolver import settle
from app.domain.awareness.timeline import AWAY_AFTER, LOST_AFTER, idle_hours
from app.domain.awareness.types import (
    RULES_VERSION,
    AwarenessDecision,
    AwarenessFact,
    AwarenessSnapshot,
)

__all__ = [
    "AWAY_AFTER",
    "LOST_AFTER",
    "RULES_VERSION",
    "VALID_INTERACTION_TYPES",
    "AwarenessDecision",
    "AwarenessFact",
    "AwarenessSnapshot",
    "decay_life",
    "food_recall_returns_home",
    "idle_hours",
    "is_valid_interaction",
    "life_band",
    "settle",
]
