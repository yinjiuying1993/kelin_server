"""Apply catalogued growth facts once. Spec §§6.4, 8.5, 15.2.

Caller must already hold `spirits` FOR UPDATE. This module never performs
network I/O; provider calls must happen in a previous unlocked transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.growth import compact_growth_payload, growth_catalog_by_event
from app.repositories import growth as growth_repo
from app.schemas.jsonb import parse_growth_payload


@dataclass(frozen=True, slots=True)
class GrowthRecord:
    event_id: uuid.UUID
    applied: bool
    spirit: growth_repo.AppliedGrowth | None


async def record_and_apply(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    event_type: str,
    source_id: uuid.UUID,
    payload: dict[str, int],
    now: datetime,
    touch_interact: bool,
) -> GrowthRecord:
    """Insert `(source_type, source_id, event_type)` then apply at most once."""
    await growth_repo.assert_writable_transaction(session)
    growth_catalog_by_event()[event_type]
    parsed = parse_growth_payload(payload)
    compact = compact_growth_payload(
        {field: value for field, value in parsed.model_dump().items() if isinstance(value, int)}
    )
    row = await growth_repo.insert_growth_event(
        session,
        spirit_id=spirit_id,
        event_type=event_type,
        source_id=source_id,
        payload=compact,
        occurred_at=now,
    )
    if not row.inserted or row.applied_at is not None:
        return GrowthRecord(event_id=row.id, applied=False, spirit=None)
    spirit = await growth_repo.apply_growth_to_spirit(
        session,
        owner_id=owner_id,
        spirit_id=spirit_id,
        payload=row.payload,
        now=now,
        touch_interact=touch_interact,
    )
    claimed = await growth_repo.mark_applied(session, event_id=row.id, now=now)
    if not claimed:
        return GrowthRecord(event_id=row.id, applied=False, spirit=None)
    return GrowthRecord(event_id=row.id, applied=True, spirit=spirit)
