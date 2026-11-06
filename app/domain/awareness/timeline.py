"""18/72h idle and study/away/visit durations. No IO."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.awareness.types import AwarenessSnapshot

AWAY_AFTER = timedelta(hours=18)
LOST_AFTER = timedelta(hours=72)
STUDY_DURATION = timedelta(hours=2)
AWAY_DURATION = timedelta(hours=3)
VISIT_COOLDOWN = timedelta(hours=12)


def require_aware(moment: datetime, *, field: str) -> datetime:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(f"{field} must be timezone-aware")
    return moment


def idle_hours(now: datetime, last_interact_at: datetime) -> int:
    require_aware(now, field="now")
    require_aware(last_interact_at, field="last_interact_at")
    elapsed = now - last_interact_at
    if elapsed < timedelta(0):
        return 0
    return elapsed // timedelta(hours=1)


def until_active(until: datetime | None, now: datetime) -> bool:
    return until is not None and until > now


def can_auto_depart_or_lose(snapshot: AwarenessSnapshot) -> bool:
    return snapshot.hatched_at is not None
