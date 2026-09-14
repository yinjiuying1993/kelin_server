from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.bootstrap import DEFAULT_ROOM_WEATHER
from app.domain.feed import (
    EMOTION_EFFECTS,
    FOOD_ENERGY_DELTA,
    FOOD_HUNGER_DELTA,
    KNOWLEDGE_CONFIDENCE,
    KNOWLEDGE_SALIENCE,
    KNOWLEDGE_SUMMARY_MAX,
    PROMISE_COMPLETE_BOND_DELTA,
    SIGHT_DAILY_LIMIT,
    clamp_trait,
    feed_request_hash,
    knowledge_summary,
    remind_at_is_future,
    room_weather_for_emotion,
)


def test_food_and_emotion_deltas_are_locked() -> None:
    assert FOOD_HUNGER_DELTA == 10
    assert FOOD_ENERGY_DELTA == 10
    assert clamp_trait(95 + 10) == 100
    assert clamp_trait(5 - 10) == 0
    assert EMOTION_EFFECTS["happy"].mood_delta == 12
    assert EMOTION_EFFECTS["calm"].mood_delta == 6
    assert EMOTION_EFFECTS["tired"].mood_delta == -8
    assert EMOTION_EFFECTS["anxious"].mood_delta == -10
    assert EMOTION_EFFECTS["angry"].mood_delta == -15


def test_emotion_weather_tokens_are_existing_room_values() -> None:
    assert room_weather_for_emotion(None) == DEFAULT_ROOM_WEATHER
    assert room_weather_for_emotion("happy") == "clear"
    assert room_weather_for_emotion("calm") == "cloudy"
    assert room_weather_for_emotion("tired") == "cloudy"
    assert room_weather_for_emotion("anxious") == "rain"
    assert room_weather_for_emotion("angry") == "rain"


def test_knowledge_summary_truncates_to_500_and_strips() -> None:
    assert knowledge_summary("  圆周率  ") == "圆周率"
    assert len(knowledge_summary("字" * 800)) == KNOWLEDGE_SUMMARY_MAX
    assert knowledge_summary("   ") == ""
    assert KNOWLEDGE_SALIENCE == 70
    assert KNOWLEDGE_CONFIDENCE == 1.0


def test_promise_complete_bond_delta_is_locked() -> None:
    assert PROMISE_COMPLETE_BOND_DELTA == 5
    assert clamp_trait(0 + PROMISE_COMPLETE_BOND_DELTA) == 5
    assert clamp_trait(98 + PROMISE_COMPLETE_BOND_DELTA) == 100
    assert SIGHT_DAILY_LIMIT == 2


def test_promise_remind_at_must_be_strictly_future() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    assert remind_at_is_future(now + timedelta(seconds=1), now)
    assert not remind_at_is_future(now, now)
    assert not remind_at_is_future(now - timedelta(seconds=1), now)


def test_feed_request_hash_ignores_client_id() -> None:
    first = feed_request_hash(kind="food", payload={})
    second = feed_request_hash(kind="food", payload={})
    assert first == second
    knowledge = feed_request_hash(kind="knowledge", payload={"text": "圆周率"})
    assert knowledge != first
