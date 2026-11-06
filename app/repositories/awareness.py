"""Owner-locked spirit_awareness persistence. Does not write emotion or growth."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.awareness.types import AwarenessSnapshot, SpiritStatus

_ENSURE_SQL = """
INSERT INTO public.spirit_awareness (
  spirit_id, status, last_interact_at, study_until, away_until,
  has_been_lost, last_state_settled_at
)
SELECT
  id, status, last_interact_at, study_until, away_until,
  has_been_lost, last_interact_at
FROM public.spirits
WHERE id = :id AND user_id = :user_id
ON CONFLICT (spirit_id) DO NOTHING
"""

_LOCK_SQL = """
SELECT
  a.spirit_id,
  a.status,
  a.last_interact_at AS awareness_last_interact,
  s.last_interact_at AS spirit_last_interact,
  a.study_until,
  a.away_until,
  a.visit_cooldown_until,
  a.has_been_lost,
  a.last_state_settled_at,
  s.hatched_at,
  COALESCE(p.timezone, 'Asia/Shanghai') AS timezone,
  EXISTS (
    SELECT 1 FROM public.pacts pa
    WHERE pa.spirit_id = s.id AND pa.status = 'active'
  ) AS has_unfinished_due_pact
FROM public.spirit_awareness a
JOIN public.spirits s ON s.id = a.spirit_id AND s.user_id = :user_id
LEFT JOIN public.user_preferences p ON p.user_id = s.user_id
FOR UPDATE OF a
"""

_APPLY_SQL = """
UPDATE public.spirit_awareness
SET
  status = :status,
  last_interact_at = :last_interact_at,
  study_until = :study_until,
  away_until = :away_until,
  visit_cooldown_until = :visit_cooldown_until,
  has_been_lost = :has_been_lost,
  last_state_settled_at = :last_state_settled_at
WHERE spirit_id = :spirit_id
RETURNING spirit_id
"""


@dataclass(frozen=True, slots=True)
class LockedAwareness:
    spirit_id: uuid.UUID
    snapshot: AwarenessSnapshot


def _as_status(value: object) -> SpiritStatus:
    text_value = str(value)
    if text_value not in {"home", "away", "study", "lost"}:
        raise ValueError("unsupported awareness status")
    return cast(SpiritStatus, text_value)


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("awareness settle cannot write in a read-only transaction")


def _earlier(left: datetime, right: datetime) -> datetime:
    return left if left <= right else right


async def lock_awareness_for_owner(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    nocturnal: int | None = None,
    timezone: str | None = None,
    has_unfinished_due_pact: bool | None = None,
) -> LockedAwareness | None:
    spirit_id = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id"),
        {"user_id": owner_id},
    )
    if spirit_id is None:
        return None
    await session.execute(text(_ENSURE_SQL), {"id": spirit_id, "user_id": owner_id})
    row = (await session.execute(text(_LOCK_SQL), {"user_id": owner_id})).first()
    if row is None:
        return None
    last_interact = _earlier(row.awareness_last_interact, row.spirit_last_interact)
    pact_due = (
        has_unfinished_due_pact
        if has_unfinished_due_pact is not None
        else bool(row.has_unfinished_due_pact)
    )
    return LockedAwareness(
        spirit_id=row.spirit_id,
        snapshot=AwarenessSnapshot(
            status=_as_status(row.status),
            hatched_at=row.hatched_at,
            last_interact_at=last_interact,
            study_until=row.study_until,
            away_until=row.away_until,
            visit_cooldown_until=row.visit_cooldown_until,
            has_unfinished_due_pact=pact_due,
            has_been_lost=bool(row.has_been_lost),
            hunger=80,
            energy=80,
            last_state_settled_at=row.last_state_settled_at,
            timezone=timezone or str(row.timezone),
            nocturnal=50 if nocturnal is None else nocturnal,
        ),
    )


async def apply_awareness_decision(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    *,
    status: str,
    last_interact_at: datetime,
    study_until: datetime | None,
    away_until: datetime | None,
    visit_cooldown_until: datetime | None,
    has_been_lost: bool,
    last_state_settled_at: datetime,
) -> uuid.UUID:
    params = {
        "status": status,
        "last_interact_at": last_interact_at,
        "study_until": study_until,
        "away_until": away_until,
        "visit_cooldown_until": visit_cooldown_until,
        "has_been_lost": has_been_lost,
        "last_state_settled_at": last_state_settled_at,
        "spirit_id": spirit_id,
    }
    value = await session.scalar(
        text(_APPLY_SQL),
        {
            "status": params["status"],
            "last_interact_at": params["last_interact_at"],
            "study_until": params["study_until"],
            "away_until": params["away_until"],
            "visit_cooldown_until": params["visit_cooldown_until"],
            "has_been_lost": params["has_been_lost"],
            "last_state_settled_at": params["last_state_settled_at"],
            "spirit_id": params["spirit_id"],
        },
    )
    if value is None:
        raise RuntimeError("awareness settle update matched no row")
    return uuid.UUID(str(value))
