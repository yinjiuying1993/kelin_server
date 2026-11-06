"""Hunger/energy decay and event deltas. No IO. Does not write mood."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.awareness.timeline import require_aware
from app.domain.awareness.types import LifeBand

HUNGER_PER_4H = 3
ENERGY_PER_4H = 2
NIGHT_PENALTY = 3
NOCTURNAL_NIGHT_THRESHOLD = 40
INITIAL_HUNGER = 80
INITIAL_ENERGY = 80

EVENT_LIFE_DELTA: dict[str, tuple[int, int]] = {
    "feed.food.accepted": (25, 10),
    "recall.completed": (25, 10),
    "feed.knowledge.accepted": (0, -1),
    "dialogue.completed": (0, -1),
    "pact.completed": (0, -3),
}


def clamp_life(value: int) -> int:
    return max(0, min(100, value))


def life_band(value: int) -> LifeBand:
    n = clamp_life(value)
    if n <= 19:
        return "L0"
    if n <= 39:
        return "L1"
    if n <= 69:
        return "L2"
    if n <= 89:
        return "L3"
    return "L4"


def elapsed_hours(now: datetime, last_state_settled_at: datetime | None) -> int:
    require_aware(now, field="now")
    if last_state_settled_at is None:
        return 0
    require_aware(last_state_settled_at, field="last_state_settled_at")
    elapsed = now - last_state_settled_at
    if elapsed < timedelta(0):
        return 0
    return elapsed // timedelta(hours=1)


def complete_night_windows(start: datetime, end: datetime, *, timezone: str) -> int:
    tz = ZoneInfo(timezone)
    start_local = start.astimezone(tz)
    end_local = end.astimezone(tz)
    if end_local <= start_local:
        return 0
    count = 0
    day = start_local.date() - timedelta(days=1)
    last = end_local.date()
    while day <= last:
        night_start = datetime.combine(day, time(23, 0), tzinfo=tz)
        night_end = datetime.combine(day + timedelta(days=1), time(8, 0), tzinfo=tz)
        if night_start >= start_local and night_end <= end_local:
            count += 1
        day += timedelta(days=1)
    return count


def decay_life(
    *,
    hunger: int,
    energy: int,
    now: datetime,
    last_state_settled_at: datetime | None,
    nocturnal: int,
    timezone: str,
) -> tuple[int, int]:
    hours = elapsed_hours(now, last_state_settled_at)
    blocks = hours // 4
    hunger = clamp_life(hunger - blocks * HUNGER_PER_4H)
    energy = clamp_life(energy - blocks * ENERGY_PER_4H)
    if nocturnal < NOCTURNAL_NIGHT_THRESHOLD and last_state_settled_at is not None:
        nights = complete_night_windows(last_state_settled_at, now, timezone=timezone)
        energy = clamp_life(energy - nights * NIGHT_PENALTY)
    return hunger, energy


def apply_event_life(
    hunger: int, energy: int, event_type: str, *, accepted: bool
) -> tuple[int, int]:
    if not accepted:
        return hunger, energy
    delta = EVENT_LIFE_DELTA.get(event_type, (0, 0))
    return clamp_life(hunger + delta[0]), clamp_life(energy + delta[1])
