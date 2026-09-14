from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from app.domain.spirit_state import (
    IDLE_LOST,
    IDLE_STUDY_OR_AWAY,
    InstantClock,
    SpiritStateSnapshot,
    SpiritStatus,
    desired_spirit_state,
    desired_spirit_state_at,
    parse_state_tick_dedupe,
    patch_equals_snapshot,
    require_aware,
    state_due_bucket,
    state_settle_dedupe_key,
    state_tick_dedupe_key,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
PLUS8 = timezone(timedelta(hours=8))
FUTURE = NOW + timedelta(hours=3)


def _snap(
    *,
    status: SpiritStatus = "home",
    idle: timedelta = timedelta(hours=1),
    study_until: datetime | None = None,
    away_until: datetime | None = None,
    has_been_lost: bool = False,
    visit_on: bool = True,
    has_due_study_task: bool = False,
) -> SpiritStateSnapshot:
    return SpiritStateSnapshot(
        status=status,
        last_interact_at=NOW - idle,
        study_until=study_until,
        away_until=away_until,
        has_been_lost=has_been_lost,
        visit_on=visit_on,
        has_due_study_task=has_due_study_task,
    )


def test_require_aware_rejects_naive_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        require_aware(datetime(2026, 9, 9, 12, 0), field="now")


def test_naive_last_interact_is_rejected() -> None:
    current = SpiritStateSnapshot(
        status="home",
        last_interact_at=datetime(2026, 9, 9, 12, 0),
        study_until=None,
        away_until=None,
        has_been_lost=False,
        visit_on=True,
        has_due_study_task=False,
    )
    with pytest.raises(ValueError, match="last_interact_at"):
        desired_spirit_state(current, NOW)


def test_naive_until_is_rejected() -> None:
    current = _snap(study_until=datetime(2026, 9, 9, 14, 0))
    with pytest.raises(ValueError, match="study_until"):
        desired_spirit_state(current, NOW)


def test_injected_clock_matches_datetime_now() -> None:
    current = _snap(idle=IDLE_STUDY_OR_AWAY, visit_on=True)
    via_clock = desired_spirit_state_at(current, InstantClock(NOW))
    via_now = desired_spirit_state(current, NOW)
    assert via_clock == via_now
    assert via_clock.status == "away"


def test_offset_clock_matches_utc_at_same_instant() -> None:
    last_plus8 = datetime(2026, 9, 9, 2, 0, tzinfo=PLUS8)
    now_plus8 = datetime(2026, 9, 9, 20, 0, tzinfo=PLUS8)
    current = SpiritStateSnapshot(
        status="home",
        last_interact_at=last_plus8,
        study_until=None,
        away_until=None,
        has_been_lost=False,
        visit_on=True,
        has_due_study_task=False,
    )
    utc_patch = desired_spirit_state(current, NOW)
    offset_patch = desired_spirit_state_at(current, InstantClock(now_plus8))
    assert utc_patch == offset_patch
    assert utc_patch.status == "away"


def test_offset_clock_just_under_18h_stays_home() -> None:
    last_plus8 = datetime(2026, 9, 9, 2, 0, 1, tzinfo=PLUS8)
    now_plus8 = datetime(2026, 9, 9, 20, 0, tzinfo=PLUS8)
    current = SpiritStateSnapshot(
        status="home",
        last_interact_at=last_plus8,
        study_until=None,
        away_until=None,
        has_been_lost=False,
        visit_on=True,
        has_due_study_task=False,
    )
    assert desired_spirit_state_at(current, InstantClock(now_plus8)).status == "home"


def test_recent_home_is_unchanged() -> None:
    current = _snap(idle=timedelta(hours=1))
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "home"
    assert patch_equals_snapshot(current, patch)


def test_just_under_18h_stays_home() -> None:
    current = _snap(idle=IDLE_STUDY_OR_AWAY - timedelta(seconds=1))
    assert desired_spirit_state(current, NOW).status == "home"


def test_1759_idle_stays_home() -> None:
    current = _snap(idle=timedelta(hours=17, minutes=59))
    assert desired_spirit_state_at(current, InstantClock(NOW)).status == "home"


def test_18h00_without_pact_and_visit_on_goes_away() -> None:
    current = _snap(idle=IDLE_STUDY_OR_AWAY, visit_on=True)
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "away"
    assert patch.away_until is None
    assert patch.study_until is None


def test_18h_visit_off_goes_study() -> None:
    current = _snap(idle=IDLE_STUDY_OR_AWAY, visit_on=False)
    assert desired_spirit_state(current, NOW).status == "study"


def test_18h_active_pact_prefers_study_over_away() -> None:
    current = _snap(idle=IDLE_STUDY_OR_AWAY, visit_on=True, has_due_study_task=True)
    assert desired_spirit_state(current, NOW).status == "study"


def test_just_under_72h_is_not_lost() -> None:
    current = _snap(idle=IDLE_LOST - timedelta(seconds=1), visit_on=True)
    assert desired_spirit_state(current, NOW).status == "away"


def test_7159_idle_is_away_not_lost() -> None:
    current = _snap(idle=timedelta(hours=71, minutes=59), visit_on=True)
    patch = desired_spirit_state_at(current, InstantClock(NOW))
    assert patch.status == "away"
    assert patch.has_been_lost is False


def test_72h00_goes_lost_and_sets_has_been_lost() -> None:
    current = _snap(idle=IDLE_LOST, visit_on=True, has_due_study_task=True)
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "lost"
    assert patch.has_been_lost is True
    assert patch.study_until is None
    assert patch.away_until is None


def test_lost_stays_lost_when_idle_is_recent() -> None:
    current = _snap(status="lost", idle=timedelta(hours=1), has_been_lost=True)
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "lost"
    assert patch_equals_snapshot(current, patch)


def test_expired_study_until_returns_home_when_idle_is_recent() -> None:
    current = _snap(
        status="study",
        idle=timedelta(hours=1),
        study_until=NOW,
    )
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "home"
    assert patch.study_until is None


def test_future_study_until_keeps_study_when_idle_is_recent() -> None:
    until = NOW + timedelta(hours=2)
    current = _snap(status="home", idle=timedelta(hours=1), study_until=until)
    patch = desired_spirit_state(current, NOW)
    assert patch.status == "study"
    assert patch.study_until == until


def test_expired_away_until_returns_home_when_idle_is_recent() -> None:
    current = _snap(
        status="away",
        idle=timedelta(hours=1),
        away_until=NOW - timedelta(seconds=1),
    )
    assert desired_spirit_state(current, NOW).status == "home"


def test_18h_beats_expired_until_home() -> None:
    current = _snap(
        status="study",
        idle=timedelta(hours=20),
        study_until=NOW - timedelta(minutes=5),
        visit_on=True,
    )
    assert desired_spirit_state(current, NOW).status == "away"


def test_future_last_interact_does_not_count_as_idle() -> None:
    current = SpiritStateSnapshot(
        status="home",
        last_interact_at=NOW + timedelta(hours=3),
        study_until=None,
        away_until=None,
        has_been_lost=False,
        visit_on=True,
        has_due_study_task=False,
    )
    assert desired_spirit_state(current, NOW).status == "home"


def test_same_moment_lost_beats_study_away_and_due_pact() -> None:
    current = _snap(
        status="study",
        idle=IDLE_LOST,
        study_until=FUTURE,
        away_until=FUTURE,
        visit_on=True,
        has_due_study_task=True,
    )
    patch = desired_spirit_state_at(current, InstantClock(NOW))
    assert patch.status == "lost"
    assert patch.study_until is None
    assert patch.away_until is None


def test_same_moment_study_until_beats_away_until() -> None:
    current = _snap(
        status="home",
        idle=timedelta(hours=1),
        study_until=FUTURE,
        away_until=FUTURE,
    )
    patch = desired_spirit_state_at(current, InstantClock(NOW))
    assert patch.status == "study"
    assert patch.study_until == FUTURE
    assert patch.away_until is None


def test_same_moment_18h_due_study_beats_visit_on() -> None:
    current = _snap(
        idle=IDLE_STUDY_OR_AWAY,
        study_until=FUTURE,
        away_until=FUTURE,
        visit_on=True,
        has_due_study_task=True,
    )
    patch = desired_spirit_state_at(current, InstantClock(NOW))
    assert patch.status == "study"
    assert patch.away_until is None


def test_until_equal_to_clock_is_expired() -> None:
    current = _snap(
        status="study",
        idle=timedelta(hours=1),
        study_until=NOW,
        away_until=NOW,
    )
    patch = desired_spirit_state_at(current, InstantClock(NOW))
    assert patch.status == "home"
    assert patch.study_until is None
    assert patch.away_until is None


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
