from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.session import READ_SNAPSHOT_SQL
from app.domain.bootstrap import (
    DEFAULT_ROOM_WEATHER,
    REPORT_UNLOCK_AFTER,
    REQUIRED_DIALOGUE_ROUNDS,
    report_eligibility_values,
)
from app.repositories import bootstrap as bootstrap_repo


def test_read_snapshot_sql_is_repeatable_read_only() -> None:
    assert "REPEATABLE READ" in READ_SNAPSHOT_SQL
    assert "READ ONLY" in READ_SNAPSHOT_SQL
    assert READ_SNAPSHOT_SQL.startswith("SET TRANSACTION")


def test_bootstrap_sql_is_fixed_and_has_no_write_locks() -> None:
    statements = (
        bootstrap_repo.SPIRIT_SQL,
        bootstrap_repo.PREFS_SQL,
        bootstrap_repo.PENDING_SIGHT_SQL,
        bootstrap_repo.UNREAD_POSTCARD_SQL,
        bootstrap_repo.DUE_PROMISE_SQL,
        bootstrap_repo.MEMORY_IDS_SQL,
        bootstrap_repo.ACTIVE_PACT_SQL,
        bootstrap_repo.REPORT_SQL,
        bootstrap_repo.USAGE_SQL,
        bootstrap_repo.LATEST_EMOTION_SQL,
    )
    blob = "\n".join(statements).upper()
    assert "FOR UPDATE" not in blob
    assert "INSERT " not in blob
    assert "UPDATE " not in blob
    assert "DELETE " not in blob
    for sql in statements:
        assert ":user_id" in sql


def test_unhatched_report_is_locked_for_seven_days() -> None:
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)
    values = report_eligibility_values(hatched_at=None, ordinary_dialogue_rounds=0, now=now)
    assert values.is_eligible is False
    assert values.eligible_at == now + REPORT_UNLOCK_AFTER
    assert values.days_remaining == 7
    assert values.required_dialogue_rounds == REQUIRED_DIALOGUE_ROUNDS
    assert values.completed_dialogue_rounds == 0
    assert values.dialogue_rounds_remaining == 10


def test_hatched_report_unlocks_after_seven_days_and_ten_rounds() -> None:
    now = datetime(2026, 9, 16, 12, tzinfo=UTC)
    hatched = datetime(2026, 9, 9, 12, tzinfo=UTC)
    locked = report_eligibility_values(hatched_at=hatched, ordinary_dialogue_rounds=3, now=now)
    assert locked.is_eligible is False
    assert locked.days_remaining == 0
    assert locked.dialogue_rounds_remaining == 7
    ready = report_eligibility_values(hatched_at=hatched, ordinary_dialogue_rounds=10, now=now)
    assert ready.is_eligible is True
    assert ready.days_remaining == 0
    assert ready.dialogue_rounds_remaining == 0


def test_days_remaining_ceils_partial_day() -> None:
    now = datetime(2026, 9, 9, 12, tzinfo=UTC)
    hatched = now - timedelta(days=6, hours=1)
    values = report_eligibility_values(hatched_at=hatched, ordinary_dialogue_rounds=0, now=now)
    assert values.is_eligible is False
    assert values.days_remaining == 1


def test_default_room_weather_is_neutral_overcast() -> None:
    assert DEFAULT_ROOM_WEATHER == "cloudy"
