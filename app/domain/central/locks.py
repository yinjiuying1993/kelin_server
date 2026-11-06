"""Fixed aggregate lock order. No IO."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.domain.central.types import LockTarget

LOCK_ORDER: tuple[LockTarget, ...] = (
    "spirit",
    "idempotency_record",
    "awareness",
    "memory",
    "projection",
)


def lock_order_for_request() -> tuple[LockTarget, ...]:
    """Same spirit mutations always lock in this order."""
    return LOCK_ORDER


def concurrent_event_key(occurred_at: datetime, event_id: uuid.UUID) -> tuple[datetime, str]:
    """Server receive time ascending, then event_id lexicographic."""
    return (occurred_at, str(event_id))


def sort_concurrent_events(
    events: tuple[tuple[datetime, uuid.UUID], ...],
) -> tuple[tuple[datetime, uuid.UUID], ...]:
    return tuple(sorted(events, key=lambda item: concurrent_event_key(item[0], item[1])))
