"""Owner-locked spirit state settle persistence. Spec §§8.6, 9.2."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.spirit_state import (
    STATE_AGGREGATE_TYPE,
    STATE_SETTLE_EVENT,
    STATE_TICK_DEDUPE_SQL,
    SpiritStatePatch,
    SpiritStateSnapshot,
    SpiritStatus,
    parse_state_tick_dedupe,
    state_settle_dedupe_key,
)
from app.schemas.social import OutboxPayload

_LOCK_SQL = """
SELECT
  s.id,
  s.status,
  s.version,
  s.last_interact_at,
  s.study_until,
  s.away_until,
  s.has_been_lost,
  COALESCE(p.visit_on, true) AS visit_on,
  EXISTS (
    SELECT 1 FROM public.pacts pa
    WHERE pa.spirit_id = s.id AND pa.status = 'active'
  ) AS has_due_study_task
FROM public.spirits s
LEFT JOIN public.user_preferences p ON p.user_id = s.user_id
WHERE s.user_id = :user_id
FOR UPDATE OF s
"""


@dataclass(frozen=True, slots=True)
class LockedSpiritState:
    spirit_id: uuid.UUID
    version: int
    last_interact_at: datetime
    snapshot: SpiritStateSnapshot


def _as_status(value: object) -> SpiritStatus:
    text_value = str(value)
    if text_value not in {"home", "away", "study", "lost"}:
        raise ValueError("unsupported spirit status")
    return cast(SpiritStatus, text_value)


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("spirit settle cannot write in a read-only transaction")


async def lock_spirit_state_for_owner(
    session: AsyncSession, owner_id: uuid.UUID
) -> LockedSpiritState | None:
    row = (await session.execute(text(_LOCK_SQL), {"user_id": owner_id})).first()
    if row is None:
        return None
    return LockedSpiritState(
        spirit_id=row.id,
        version=int(row.version),
        last_interact_at=row.last_interact_at,
        snapshot=SpiritStateSnapshot(
            status=_as_status(row.status),
            last_interact_at=row.last_interact_at,
            study_until=row.study_until,
            away_until=row.away_until,
            has_been_lost=bool(row.has_been_lost),
            visit_on=bool(row.visit_on),
            has_due_study_task=bool(row.has_due_study_task),
        ),
    )


async def apply_spirit_state_patch(
    session: AsyncSession,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    patch: SpiritStatePatch,
    *,
    expected_version: int,
) -> int:
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits "
                "SET status = :status, study_until = :study_until, "
                "away_until = :away_until, has_been_lost = :has_been_lost, "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND version = :expected_version "
                "RETURNING version"
            ),
            {
                "status": patch.status,
                "study_until": patch.study_until,
                "away_until": patch.away_until,
                "has_been_lost": patch.has_been_lost,
                "id": spirit_id,
                "user_id": owner_id,
                "expected_version": expected_version,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("spirit settle update matched no owner row")
    return int(row.version)


async def insert_state_settle_outbox(
    session: AsyncSession,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    *,
    from_status: str,
    to_status: str,
    version: int,
) -> None:
    payload: dict[str, Any] = OutboxPayload(resource_id=spirit_id).model_dump(
        mode="json", exclude_none=True
    )
    await session.execute(
        text(
            "INSERT INTO public.outbox_events ("
            "aggregate_type, aggregate_id, event_type, dedupe_key, owner_id, payload"
            ") VALUES ("
            ":aggregate_type, :aggregate_id, :event_type, :dedupe_key, :owner_id, "
            "CAST(:payload AS jsonb)"
            ")"
        ),
        {
            "aggregate_type": STATE_AGGREGATE_TYPE,
            "aggregate_id": spirit_id,
            "event_type": STATE_SETTLE_EVENT,
            "dedupe_key": state_settle_dedupe_key(
                spirit_id,
                from_status=from_status,
                to_status=to_status,
                version=version,
            ),
            "owner_id": owner_id,
            "payload": json.dumps(payload),
        },
    )


async def enqueue_due_state_jobs(session: AsyncSession, *, now: datetime) -> int:
    inserted = await session.scalar(
        text("SELECT private.enqueue_due_state_jobs(:now)"),
        {"now": now},
    )
    return int(inserted or 0)


@dataclass(frozen=True, slots=True)
class StateTickJob:
    id: uuid.UUID
    owner_id: uuid.UUID
    spirit_id: uuid.UUID
    dedupe_key: str


async def claim_due_state_ticks(
    session: AsyncSession, *, now: datetime, limit: int = 50
) -> tuple[StateTickJob, ...]:
    rows = (
        await session.execute(
            text(
                "WITH picked AS ("
                "SELECT id FROM public.outbox_events "
                "WHERE event_type = :event_type "
                "AND status IN ('pending', 'retry') "
                "AND available_at <= :now "
                "AND owner_id IS NOT NULL "
                "AND dedupe_key ~ :pattern "
                "ORDER BY available_at ASC, id ASC "
                "FOR UPDATE SKIP LOCKED "
                "LIMIT :limit"
                ") UPDATE public.outbox_events AS o "
                "SET status = 'claimed', locked_at = :now "
                "FROM picked WHERE o.id = picked.id "
                "RETURNING o.id, o.owner_id, o.aggregate_id, o.dedupe_key"
            ),
            {
                "event_type": STATE_SETTLE_EVENT,
                "now": now,
                "pattern": STATE_TICK_DEDUPE_SQL,
                "limit": limit,
            },
        )
    ).all()
    jobs: list[StateTickJob] = []
    for row in rows:
        parsed = parse_state_tick_dedupe(str(row.dedupe_key))
        if parsed is None or row.owner_id is None:
            continue
        jobs.append(
            StateTickJob(
                id=uuid.UUID(str(row.id)),
                owner_id=uuid.UUID(str(row.owner_id)),
                spirit_id=uuid.UUID(str(row.aggregate_id)),
                dedupe_key=str(row.dedupe_key),
            )
        )
    return tuple(jobs)


async def mark_state_tick_done(session: AsyncSession, *, job_id: uuid.UUID, now: datetime) -> bool:
    marked = await session.scalar(
        text(
            "UPDATE public.outbox_events SET status = 'done', processed_at = :now "
            "WHERE id = :id AND status IN ('pending', 'retry', 'claimed') "
            "RETURNING id"
        ),
        {"id": job_id, "now": now},
    )
    return marked is not None
