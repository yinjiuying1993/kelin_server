"""Scheduler/outbox helpers for spirit state ticks. 18/72 lives in domain.awareness."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from re import compile as re_compile
from typing import Literal, Protocol
from uuid import UUID

SpiritStatus = Literal["home", "away", "study", "lost"]

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
