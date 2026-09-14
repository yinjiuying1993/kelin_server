"""Bootstrap snapshot projection helpers. Spec §§5.5, 8.10, 9.1–9.2, 8.9.1."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.spirit_state import require_aware

REPORT_UNLOCK_AFTER = timedelta(days=7)
REQUIRED_DIALOGUE_ROUNDS = 10
DEFAULT_ROOM_WEATHER = "cloudy"
V0_IMAGE_FEED = True
LATEST_MEMORY_LIMIT = 20
SECONDS_PER_DAY = 86400


@dataclass(frozen=True, slots=True)
class ReportEligibilityValues:
    is_eligible: bool
    eligible_at: datetime
    days_remaining: int
    required_dialogue_rounds: int
    completed_dialogue_rounds: int
    dialogue_rounds_remaining: int


def utc_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_z_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return utc_z(value)


def local_date(now: datetime, timezone_name: str) -> date:
    require_aware(now, field="now")
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = ZoneInfo("Asia/Shanghai")
    return now.astimezone(zone).date()


def next_local_midnight(now: datetime, timezone_name: str) -> datetime:
    require_aware(now, field="now")
    try:
        zone = ZoneInfo(timezone_name)
    except Exception:
        zone = ZoneInfo("Asia/Shanghai")
    local = now.astimezone(zone)
    nxt = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
    return nxt.astimezone(UTC)


def _ceil_days(delta: timedelta) -> int:
    seconds = delta.total_seconds()
    if seconds <= 0:
        return 0
    return int((seconds + SECONDS_PER_DAY - 1) // SECONDS_PER_DAY)


def report_eligibility_values(
    *,
    hatched_at: datetime | None,
    ordinary_dialogue_rounds: int,
    now: datetime,
) -> ReportEligibilityValues:
    require_aware(now, field="now")
    completed = max(0, ordinary_dialogue_rounds)
    if hatched_at is None:
        eligible_at = now + REPORT_UNLOCK_AFTER
        return ReportEligibilityValues(
            is_eligible=False,
            eligible_at=eligible_at,
            days_remaining=_ceil_days(eligible_at - now),
            required_dialogue_rounds=REQUIRED_DIALOGUE_ROUNDS,
            completed_dialogue_rounds=completed,
            dialogue_rounds_remaining=max(0, REQUIRED_DIALOGUE_ROUNDS - completed),
        )
    require_aware(hatched_at, field="hatched_at")
    eligible_at = hatched_at + REPORT_UNLOCK_AFTER
    unlocked = now >= eligible_at
    rounds_ok = completed >= REQUIRED_DIALOGUE_ROUNDS
    return ReportEligibilityValues(
        is_eligible=unlocked and rounds_ok,
        eligible_at=eligible_at,
        days_remaining=0 if unlocked else _ceil_days(eligible_at - now),
        required_dialogue_rounds=REQUIRED_DIALOGUE_ROUNDS,
        completed_dialogue_rounds=completed,
        dialogue_rounds_remaining=max(0, REQUIRED_DIALOGUE_ROUNDS - completed),
    )
