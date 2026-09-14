from __future__ import annotations

from datetime import UTC, date, datetime
from inspect import getsource
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.domain.feed import (
    EMOTION_DAILY_LIMIT,
    FOOD_DAILY_LIMIT,
    KNOWLEDGE_DAILY_LIMIT,
    SIGHT_DAILY_LIMIT,
)
from app.domain.quota import (
    ASR_DAILY_LIMIT,
    CHAT_DAILY_LIMIT,
    DEFAULT_DAILY_LIMITS,
    DEFAULT_TIMEZONE,
    PACT_DAILY_LIMIT,
    QUOTA_CAPABILITIES,
    SEARCH_DAILY_LIMIT,
    TTS_DAILY_LIMIT,
    VISIT_DAILY_LIMIT,
    QuotaSnapshot,
    coerce_timezone,
    daily_limit,
    exceeded_details,
    local_usage_date,
    quota_usage,
    require_capability,
    reservation_dedupe_key,
    usage_reset_at,
)
from app.repositories import bootstrap as bootstrap_repo
from app.repositories import quota as quota_repo
from pytest import raises

NOW = datetime(2026, 9, 10, 2, 0, tzinfo=UTC)


def test_catalog_locks_feed_limits_and_named_defaults() -> None:
    assert QUOTA_CAPABILITIES == frozenset(DEFAULT_DAILY_LIMITS)
    assert daily_limit("food") == FOOD_DAILY_LIMIT == 3
    assert daily_limit("sight") == SIGHT_DAILY_LIMIT == 2
    assert daily_limit("knowledge") == KNOWLEDGE_DAILY_LIMIT == 10
    assert daily_limit("emotion") == EMOTION_DAILY_LIMIT == 10
    assert daily_limit("chat") == CHAT_DAILY_LIMIT == 100
    assert daily_limit("asr") == ASR_DAILY_LIMIT == 60
    assert daily_limit("tts") == TTS_DAILY_LIMIT == 20
    assert daily_limit("search") == SEARCH_DAILY_LIMIT == 20
    assert daily_limit("visit") == VISIT_DAILY_LIMIT == 3
    assert daily_limit("pact") == PACT_DAILY_LIMIT == 1


def test_unknown_capability_is_rejected() -> None:
    with raises(ValueError, match="catalog"):
        require_capability("prompt")


def test_invalid_timezone_falls_back_to_shanghai() -> None:
    assert coerce_timezone(None) == DEFAULT_TIMEZONE
    assert coerce_timezone("not/a-zone") == DEFAULT_TIMEZONE
    assert coerce_timezone("America/Los_Angeles") == "America/Los_Angeles"


def test_usage_reset_at_is_next_local_midnight_in_utc() -> None:
    reset = usage_reset_at(date(2026, 9, 9), "Asia/Shanghai")
    assert reset == datetime(2026, 9, 9, 16, 0, tzinfo=UTC)
    assert (
        quota_usage(
            QuotaSnapshot(
                capability="food",
                used=1,
                reserved=0,
                limit=3,
                usage_date=date(2026, 9, 9),
                timezone="Asia/Shanghai",
                version=2,
            )
        ).reset_at
        == "2026-09-09T16:00:00Z"
    )


def test_timezone_hop_would_mint_a_different_local_date() -> None:
    shanghai = local_usage_date(NOW, "Asia/Shanghai")
    los_angeles = local_usage_date(NOW, "America/Los_Angeles")
    assert shanghai == date(2026, 9, 10)
    assert los_angeles == date(2026, 9, 9)
    assert shanghai != los_angeles


def test_exceeded_details_are_stable() -> None:
    snapshot = QuotaSnapshot(
        capability="knowledge",
        used=10,
        reserved=0,
        limit=10,
        usage_date=date(2026, 9, 9),
        timezone="America/Los_Angeles",
        version=11,
    )
    details = exceeded_details(snapshot)
    assert details == {
        "quota": "knowledge",
        "reset_at": quota_usage(snapshot).reset_at,
    }
    assert details["reset_at"].endswith("Z")


def test_reservation_dedupe_key_is_owner_capability_source() -> None:
    owner_id = uuid4()
    source_id = uuid4()
    assert (
        reservation_dedupe_key(owner_id=owner_id, capability="chat", source_id=source_id)
        == f"quota:{owner_id}:chat:{source_id}"
    )


def test_consume_sql_is_conditional_update_not_read_modify_write() -> None:
    source = getsource(quota_repo.consume)
    assert "used + reserved + :amount <= limit_value" in source
    assert "ON CONFLICT (user_id, usage_date, capability) DO NOTHING" in getsource(
        quota_repo.ensure_usage_row
    )
    assert "used = used + :amount" in source
    active = quota_repo._ACTIVE_WINDOW_SQL
    assert "AT TIME ZONE timezone" in active
    assert "ORDER BY usage_date DESC" in active


def test_bootstrap_usage_sql_reuses_open_timezone_window() -> None:
    assert "AT TIME ZONE timezone" in bootstrap_repo.USAGE_SQL
    assert ":now" in bootstrap_repo.USAGE_SQL
    assert "FOR UPDATE" not in bootstrap_repo.USAGE_SQL.upper()


def test_aware_now_is_required_for_local_usage_date() -> None:
    with raises(ValueError, match="now"):
        local_usage_date(datetime(2026, 9, 10, 2, 0), "Asia/Shanghai")
    zone = ZoneInfo("Asia/Shanghai")
    assert local_usage_date(NOW.astimezone(zone), "Asia/Shanghai") == date(2026, 9, 10)
