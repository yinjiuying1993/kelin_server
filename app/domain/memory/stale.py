"""Choose active memories that have gone stale. Does not delete rows."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.memory.constants import TYPE_SALIENCE_FLOOR

STALE_UNCONFIRMED_DAYS = 90


@dataclass(frozen=True, slots=True)
class StaleCandidate:
    memory_id: uuid.UUID
    memory_type: str
    salience: int
    last_confirmed_at: datetime | None
    status: str = "active"


def stale_memory_ids(
    rows: tuple[StaleCandidate, ...],
    *,
    now: datetime,
) -> tuple[uuid.UUID, ...]:
    """Active, confirmed more than 90 days ago, and under the type floor."""
    if now.tzinfo is None:
        raise ValueError("now")
    cutoff = now - timedelta(days=STALE_UNCONFIRMED_DAYS)
    chosen: list[uuid.UUID] = []
    for row in rows:
        if row.status != "active":
            continue
        confirmed = row.last_confirmed_at
        if confirmed is None or confirmed.tzinfo is None or confirmed >= cutoff:
            continue
        floor = TYPE_SALIENCE_FLOOR.get(row.memory_type)
        if floor is None or row.salience >= floor:
            continue
        chosen.append(row.memory_id)
    return tuple(chosen)
