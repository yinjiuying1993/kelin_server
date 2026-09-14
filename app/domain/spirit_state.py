"""18h/72h and until-expiry spirit status. Spec §§8.1, 8.6, 9.2.

Bootstrap is not an effective interaction and must not change last_interact_at.
Entering study/away from idle does not invent until timestamps (duration is unspecified).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from re import compile as re_compile
from typing import Literal, Protocol
from uuid import UUID

SpiritStatus = Literal["home", "away", "study", "lost"]

IDLE_STUDY_OR_AWAY = timedelta(hours=18)
IDLE_LOST = timedelta(hours=72)
STATE_AGGREGATE_TYPE = "spirit"
STATE_SETTLE_EVENT = "state.settle"
STATE_TICK_DEDUPE = re_compile(
    r"^state:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([0-9]{10})$"
)
STATE_TICK_DEDUPE_SQL = (
    r"^state:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]{10}$"
)


@dataclass(frozen=True, slots=True)
class SpiritStateSnapshot:
    status: SpiritStatus
    last_interact_at: datetime
    study_until: datetime | None
    away_until: datetime | None
    has_been_lost: bool
    visit_on: bool
    has_due_study_task: bool


@dataclass(frozen=True, slots=True)
class SpiritStatePatch:
    status: SpiritStatus
    study_until: datetime | None
    away_until: datetime | None
    has_been_lost: bool


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware settle instant; tests inject a fixed clock."""


@dataclass(frozen=True, slots=True)
class InstantClock:
    instant: datetime

    def now(self) -> datetime:
        return require_aware(self.instant, field="now")


def require_aware(moment: datetime, *, field: str) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return moment


def _until_active(until: datetime | None, now: datetime) -> bool:
    return until is not None and until > now


def _kept_until(until: datetime | None, now: datetime) -> datetime | None:
    return until if _until_active(until, now) else None


def desired_spirit_state_at(current: SpiritStateSnapshot, clock: Clock) -> SpiritStatePatch:
    return desired_spirit_state(current, clock.now())


def desired_spirit_state(current: SpiritStateSnapshot, now: datetime) -> SpiritStatePatch:
    """Priority: lost > study > away > home. Idle uses last_interact_at only."""

    require_aware(now, field="now")
    require_aware(current.last_interact_at, field="last_interact_at")
    if current.study_until is not None:
        require_aware(current.study_until, field="study_until")
    if current.away_until is not None:
        require_aware(current.away_until, field="away_until")
    idle = now - current.last_interact_at
    if idle < timedelta(0):
        idle = timedelta(0)

    if idle >= IDLE_LOST:
        return SpiritStatePatch(
            status="lost",
            study_until=None,
            away_until=None,
            has_been_lost=True,
        )

    if idle >= IDLE_STUDY_OR_AWAY:
        if current.has_due_study_task:
            return SpiritStatePatch(
                status="study",
                study_until=_kept_until(current.study_until, now),
                away_until=None,
                has_been_lost=current.has_been_lost,
            )
        if current.visit_on:
            return SpiritStatePatch(
                status="away",
                study_until=None,
                away_until=_kept_until(current.away_until, now),
                has_been_lost=current.has_been_lost,
            )
        return SpiritStatePatch(
            status="study",
            study_until=_kept_until(current.study_until, now),
            away_until=None,
            has_been_lost=current.has_been_lost,
        )

    if current.status == "lost":
        return SpiritStatePatch(
            status="lost",
            study_until=None,
            away_until=None,
            has_been_lost=True,
        )

    if _until_active(current.study_until, now):
        return SpiritStatePatch(
            status="study",
            study_until=current.study_until,
            away_until=None,
            has_been_lost=current.has_been_lost,
        )
    if _until_active(current.away_until, now):
        return SpiritStatePatch(
            status="away",
            study_until=None,
            away_until=current.away_until,
            has_been_lost=current.has_been_lost,
        )
    return SpiritStatePatch(
        status="home",
        study_until=None,
        away_until=None,
        has_been_lost=current.has_been_lost,
    )


def patch_equals_snapshot(current: SpiritStateSnapshot, patch: SpiritStatePatch) -> bool:
    return (
        current.status == patch.status
        and current.study_until == patch.study_until
        and current.away_until == patch.away_until
        and current.has_been_lost == patch.has_been_lost
    )


def state_settle_dedupe_key(
    spirit_id: object,
    *,
    from_status: str,
    to_status: str,
    version: int,
) -> str:
    return f"state:{spirit_id}:{from_status}:{to_status}:{version}"


def state_due_bucket(now: datetime) -> str:
    aware = require_aware(now, field="now")
    utc = aware.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return utc.strftime("%Y%m%d%H")


def state_tick_dedupe_key(spirit_id: object, *, now: datetime) -> str:
    return f"state:{spirit_id}:{state_due_bucket(now)}"


def parse_state_tick_dedupe(key: str) -> tuple[UUID, str] | None:
    matched = STATE_TICK_DEDUPE.fullmatch(key)
    if matched is None:
        return None
    return UUID(matched.group(1)), matched.group(2)
