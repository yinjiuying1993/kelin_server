"""Choose away after idle >= 18h. Study is no longer a destination. No IO."""

from __future__ import annotations

from datetime import datetime

from app.domain.awareness.timeline import AWAY_DURATION, until_active
from app.domain.awareness.types import AwarenessSnapshot, SpiritStatus


def choose_departure(
    snapshot: AwarenessSnapshot,
    now: datetime,
    *,
    cooldown_until: datetime | None,
) -> tuple[SpiritStatus, datetime | None, datetime | None, bool]:
    """Return status, study_until, away_until, destination_plan_required.

    A due pact does not choose study. ``snapshot`` stays in the signature for callers.
    """
    del snapshot
    if until_active(cooldown_until, now):
        return "home", None, None, False
    return "away", None, now + AWAY_DURATION, True
