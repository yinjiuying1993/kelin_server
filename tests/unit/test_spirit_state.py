from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.domain.spirit_state import (
    InstantClock,
    parse_state_tick_dedupe,
    require_aware,
    state_due_bucket,
    state_settle_dedupe_key,
    state_tick_dedupe_key,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PLUS8 = timezone(timedelta(hours=8))


def test_require_aware_rejects_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware(datetime(2026, 9, 9, 12, 0), field="now")


def test_instant_clock_rejects_naive_instant() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        InstantClock(datetime(2026, 9, 9, 12, 0)).now()


def test_domain_has_no_18_72_machine() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "app" / "domain" / "spirit_state.py").read_text(
        encoding="utf-8"
    )
    assert "IDLE_STUDY_OR_AWAY" not in source
    assert "IDLE_LOST" not in source
    assert "desired_spirit_state" not in source
    assert "timedelta(hours=18)" not in source
    assert "timedelta(hours=72)" not in source


def test_dedupe_key_includes_transition_and_version() -> None:
    key = state_settle_dedupe_key(
        "11111111-1111-1111-1111-111111111111",
        from_status="home",
        to_status="lost",
        version=2,
    )
    assert key == "state:11111111-1111-1111-1111-111111111111:home:lost:2"
    assert parse_state_tick_dedupe(key) is None


def test_tick_dedupe_is_utc_hour_bucket_and_not_settle_key() -> None:
    spirit_id = "11111111-1111-1111-1111-111111111111"
    assert state_due_bucket(NOW) == "2026090912"
    assert state_due_bucket(datetime(2026, 9, 9, 20, 0, tzinfo=PLUS8)) == "2026090912"
    assert state_due_bucket(NOW.replace(minute=59, second=59)) == "2026090912"
    assert state_due_bucket(NOW + timedelta(hours=1)) == "2026090913"
    tick = state_tick_dedupe_key(spirit_id, now=NOW)
    assert tick == "state:11111111-1111-1111-1111-111111111111:2026090912"
    parsed = parse_state_tick_dedupe(tick)
    assert parsed is not None
    assert str(parsed[0]) == spirit_id
    assert parsed[1] == "2026090912"
