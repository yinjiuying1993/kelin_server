"""Food/knowledge/emotion settlement constants. Spec §8.4; P12-T01 locked values."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.bootstrap import DEFAULT_ROOM_WEATHER

FEED_CREATE_OPERATION = "feeds.create"
FEED_PATCH_OPERATION = "feeds.patch"
FEED_COMPLETE_OPERATION = "feeds.complete"
FEED_CANCEL_OPERATION = "feeds.cancel"
SETTLEABLE_KINDS = frozenset({"food", "knowledge", "emotion", "promise", "sight"})
FOOD_HUNGER_DELTA = 10
FOOD_ENERGY_DELTA = 10
FOOD_DAILY_LIMIT = 3
SIGHT_DAILY_LIMIT = 2
PROMISE_COMPLETE_BOND_DELTA = 5
KNOWLEDGE_DAILY_LIMIT = 10
EMOTION_DAILY_LIMIT = 10
KNOWLEDGE_SUMMARY_MAX = 500
KNOWLEDGE_SALIENCE = 70
KNOWLEDGE_CONFIDENCE = 1.0
TRAIT_MIN = 0
TRAIT_MAX = 100
FEED_EVENT_TYPE = "feed.accepted"
PROMISE_COMPLETED_EVENT_TYPE = "promise.completed"
GROWTH_SOURCE_TYPE = "feed"
GROWTH_EVENT_TYPE = "feed_accepted"
GROWTH_PROMISE_COMPLETED = "promise_completed"


@dataclass(frozen=True, slots=True)
class EmotionEffect:
    mood_delta: int
    weather: str


EMOTION_EFFECTS: dict[str, EmotionEffect] = {
    "happy": EmotionEffect(mood_delta=12, weather="clear"),
    "calm": EmotionEffect(mood_delta=6, weather="cloudy"),
    "tired": EmotionEffect(mood_delta=-8, weather="cloudy"),
    "anxious": EmotionEffect(mood_delta=-10, weather="rain"),
    "angry": EmotionEffect(mood_delta=-15, weather="rain"),
}

DAILY_LIMITS: dict[str, int] = {
    "food": FOOD_DAILY_LIMIT,
    "knowledge": KNOWLEDGE_DAILY_LIMIT,
    "emotion": EMOTION_DAILY_LIMIT,
    "sight": SIGHT_DAILY_LIMIT,
}


def clamp_trait(value: int) -> int:
    return max(TRAIT_MIN, min(TRAIT_MAX, value))


def room_weather_for_emotion(emotion: str | None) -> str:
    if emotion is None:
        return DEFAULT_ROOM_WEATHER
    effect = EMOTION_EFFECTS.get(emotion)
    if effect is None:
        return DEFAULT_ROOM_WEATHER
    return effect.weather


def knowledge_summary(text: str) -> str:
    return text.strip()[:KNOWLEDGE_SUMMARY_MAX]


def feed_request_hash(*, kind: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"kind": kind, "payload": payload},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def remind_at_is_future(remind_at: datetime, now: datetime) -> bool:
    return remind_at > now


def promise_mutation_hash(
    *,
    action: str,
    feed_id: UUID,
    expected_version: int,
    text: str | None = None,
    remind_at: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "action": action,
        "expected_version": expected_version,
        "feed_id": str(feed_id),
        "remind_at": remind_at,
        "text": text,
    }
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
