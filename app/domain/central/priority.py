"""Single copy of conflict priority. Other modules must not duplicate this."""

from __future__ import annotations

from typing import Literal

from app.domain.awareness.statuses import without_study
from app.domain.central.types import SpiritStatus

MemoryLifecycle = Literal["active", "sealed", "deleted", "superseded", "stale"]
TraitCandidate = Literal[
    "user_negative_boundary",
    "user_positive_preference",
    "relationship_event",
    "learning",
    "period_stats",
]
KnowledgeSource = Literal[
    "private_knowledge",
    "current_pact_or_note",
    "builtin_bank",
    "network_if_enabled",
]
ChatInfluence = Literal[
    "safety",
    "status_limit",
    "primary_emotion",
    "personality_band",
    "memory_whitelist",
    "time_of_day",
]

_STATUS_RANK: dict[SpiritStatus, int] = {
    "lost": 0,
    "study": 1,
    "away": 2,
    "home": 3,
}
_MEMORY_RANK: dict[MemoryLifecycle, int] = {
    "deleted": 0,
    "sealed": 1,
    "stale": 2,
    "superseded": 3,
    "active": 4,
}
_TRAIT_RANK: dict[TraitCandidate, int] = {
    "user_negative_boundary": 0,
    "user_positive_preference": 1,
    "relationship_event": 2,
    "learning": 3,
    "period_stats": 4,
}
_KNOWLEDGE_RANK: dict[KnowledgeSource, int] = {
    "private_knowledge": 0,
    "current_pact_or_note": 1,
    "builtin_bank": 2,
    "network_if_enabled": 3,
}
_CHAT_RANK: dict[ChatInfluence, int] = {
    "safety": 0,
    "status_limit": 1,
    "primary_emotion": 2,
    "personality_band": 3,
    "memory_whitelist": 4,
    "time_of_day": 5,
}

_EMOTION_ALIASES: dict[str, str] = {
    "不安": "uneasy",
    "lost": "uneasy",
    "uneasy": "uneasy",
    "委屈": "hurt",
    "hurt": "hurt",
    "just_recalled": "hurt",
    "又饿又困": "hungry_and_tired",
    "hungry_and_tired": "hungry_and_tired",
    "饿了": "hungry",
    "hungry": "hungry",
    "困了": "tired",
    "tired": "tired",
    "平静": "calm",
    "calm": "calm",
}

_EMOTION_RANK = {
    "uneasy": 0,
    "hurt": 1,
    "hungry_and_tired": 2,
    "hungry": 3,
    "tired": 4,
    "calm": 100,
}


def _best[T](candidates: tuple[T, ...], rank: dict[T, int]) -> T:
    if not candidates:
        raise ValueError("candidates required")
    return min(candidates, key=lambda item: rank[item])


def winner_status(candidates: tuple[SpiritStatus, ...]) -> SpiritStatus:
    shown = tuple(without_study(item) for item in candidates)
    return _best(shown, _STATUS_RANK)


def winner_memory_lifecycle(candidates: tuple[MemoryLifecycle, ...]) -> MemoryLifecycle:
    return _best(candidates, _MEMORY_RANK)


def normalize_emotion_label(label: str) -> str:
    return _EMOTION_ALIASES.get(label, label)


def winner_emotion_label(candidates: tuple[str, ...]) -> str:
    if not candidates:
        raise ValueError("candidates required")
    return min(
        candidates,
        key=lambda item: _EMOTION_RANK.get(normalize_emotion_label(item), 50),
    )


def winner_trait_candidate(candidates: tuple[TraitCandidate, ...]) -> TraitCandidate:
    return _best(candidates, _TRAIT_RANK)


def winner_knowledge_source(candidates: tuple[KnowledgeSource, ...]) -> KnowledgeSource:
    return _best(candidates, _KNOWLEDGE_RANK)


def winner_chat_influence(candidates: tuple[ChatInfluence, ...]) -> ChatInfluence:
    return _best(candidates, _CHAT_RANK)


def memory_may_resurrect(*, lifecycle: MemoryLifecycle) -> bool:
    return lifecycle == "active"
