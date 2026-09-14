"""Visit planner persistence. Spec §§7.4, 8.9, 17.2."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.visits import (
    VISIT_PLAN_DEDUPE_SQL,
    VISIT_PLAN_EVENT,
    parse_visit_plan_dedupe,
    parse_visit_settle_dedupe,
)


@dataclass(frozen=True, slots=True)
class VisitPlanJob:
    id: uuid.UUID
    owner_id: uuid.UUID
    spirit_id: uuid.UUID
    dedupe_key: str


@dataclass(frozen=True, slots=True)
class VisitSettleJob:
    id: uuid.UUID
    visit_id: uuid.UUID
    owner_id: uuid.UUID | None
    dedupe_key: str
    locked_by: str


@dataclass(frozen=True, slots=True)
class VisitPostcardPublicInput:
    visit_id: uuid.UUID
    visitor_id: uuid.UUID
    host_id: uuid.UUID | None
    npc_id: str | None
    dest_title: str
    dest_stage: str
    dest_weather: str
    dest_marks: tuple[str, ...]
    visitor_title: str
    visitor_stage: str
    visitor_weather: str
    visitor_marks: tuple[str, ...]


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("visit planner cannot write in a read-only transaction")


async def enqueue_due_visit_plan_jobs(session: AsyncSession, *, now: datetime) -> int:
    inserted = await session.scalar(
        text("SELECT private.enqueue_due_visit_plan_jobs(:now)"),
        {"now": now},
    )
    return int(inserted or 0)


async def plan_visits(session: AsyncSession, *, now: datetime) -> int:
    inserted = await session.scalar(
        text("SELECT private.plan_visits(:now)"),
        {"now": now},
    )
    return int(inserted or 0)


async def claim_due_visit_plan_jobs(
    session: AsyncSession, *, now: datetime, limit: int = 50
) -> tuple[VisitPlanJob, ...]:
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
                "event_type": VISIT_PLAN_EVENT,
                "now": now,
                "pattern": VISIT_PLAN_DEDUPE_SQL,
                "limit": limit,
            },
        )
    ).all()
    jobs: list[VisitPlanJob] = []
    for row in rows:
        parsed = parse_visit_plan_dedupe(str(row.dedupe_key))
        if parsed is None or row.owner_id is None:
            continue
        jobs.append(
            VisitPlanJob(
                id=uuid.UUID(str(row.id)),
                owner_id=uuid.UUID(str(row.owner_id)),
                spirit_id=parsed[0],
                dedupe_key=str(row.dedupe_key),
            )
        )
    return tuple(jobs)


async def mark_visit_plan_done(session: AsyncSession, *, job_id: uuid.UUID, now: datetime) -> bool:
    marked = await session.scalar(
        text(
            "UPDATE public.outbox_events SET status = 'done', processed_at = :now "
            "WHERE id = :id AND status IN ('pending', 'retry', 'claimed') "
            "RETURNING id"
        ),
        {"id": job_id, "now": now},
    )
    return marked is not None


async def enqueue_due_visit_settle_jobs(session: AsyncSession, *, now: datetime) -> int:
    inserted = await session.scalar(
        text("SELECT private.enqueue_due_visit_settle_jobs(:now)"),
        {"now": now},
    )
    return int(inserted or 0)


async def claim_due_visit_settle_jobs(
    session: AsyncSession,
    *,
    now: datetime,
    worker_id: str,
    limit: int = 50,
    lease_seconds: int = 120,
) -> tuple[VisitSettleJob, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT job_id, visit_id, owner_id, dedupe_key, locked_by "
                "FROM private.claim_due_visit_settle_jobs("
                ":now, :limit, :worker_id, :lease_seconds)"
            ),
            {
                "now": now,
                "limit": limit,
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
            },
        )
    ).all()
    jobs: list[VisitSettleJob] = []
    for row in rows:
        visit_id = parse_visit_settle_dedupe(str(row.dedupe_key))
        if visit_id is None:
            continue
        jobs.append(
            VisitSettleJob(
                id=uuid.UUID(str(row.job_id)),
                visit_id=uuid.UUID(str(row.visit_id)),
                owner_id=uuid.UUID(str(row.owner_id)) if row.owner_id is not None else None,
                dedupe_key=str(row.dedupe_key),
                locked_by=str(row.locked_by),
            )
        )
    return tuple(jobs)


async def load_visit_postcard_public_input(
    session: AsyncSession, *, visit_id: uuid.UUID
) -> VisitPostcardPublicInput | None:
    row = (
        await session.execute(
            text(
                "SELECT visit_id, visitor_id, host_id, npc_id, dest_title, dest_stage, "
                "dest_weather, dest_marks, visitor_title, visitor_stage, visitor_weather, "
                "visitor_marks FROM private.visit_postcard_public_input(:visit_id)"
            ),
            {"visit_id": visit_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return VisitPostcardPublicInput(
        visit_id=uuid.UUID(str(row.visit_id)),
        visitor_id=uuid.UUID(str(row.visitor_id)),
        host_id=uuid.UUID(str(row.host_id)) if row.host_id is not None else None,
        npc_id=str(row.npc_id) if row.npc_id is not None else None,
        dest_title=str(row.dest_title),
        dest_stage=str(row.dest_stage),
        dest_weather=str(row.dest_weather),
        dest_marks=_json_marks(row.dest_marks),
        visitor_title=str(row.visitor_title),
        visitor_stage=str(row.visitor_stage),
        visitor_weather=str(row.visitor_weather),
        visitor_marks=_json_marks(row.visitor_marks),
    )


def _json_marks(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        loaded = json.loads(value)
        if isinstance(loaded, list):
            return tuple(str(item) for item in loaded)
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


async def settle_visit(
    session: AsyncSession,
    *,
    visit_id: uuid.UUID,
    now: datetime,
    visitor_text: str = "",
    host_text: str = "",
) -> int:
    changed = await session.scalar(
        text(
            "SELECT private.settle_visit(:visit_id, :now, :visitor_text, :host_text)"
        ),
        {
            "visit_id": visit_id,
            "now": now,
            "visitor_text": visitor_text,
            "host_text": host_text,
        },
    )
    return int(changed or 0)


async def reclaim_expired_outbox(session: AsyncSession, *, now: datetime) -> int:
    updated = await session.scalar(
        text("SELECT private.reclaim_expired_outbox(:now)"),
        {"now": now},
    )
    return int(updated or 0)


async def sync_dead_visit_settles(session: AsyncSession, *, now: datetime) -> int:
    updated = await session.scalar(
        text("SELECT private.sync_dead_visit_settles(:now)"),
        {"now": now},
    )
    return int(updated or 0)


async def mark_visit_settle_retry(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    worker_id: str,
    now: datetime,
    error_code: str,
) -> str | None:
    status = await session.scalar(
        text(
            "UPDATE public.outbox_events "
            "SET status = CASE WHEN attempt_count >= max_attempts THEN 'dead' ELSE 'retry' END, "
            "locked_by = NULL, locked_at = NULL, lease_expires_at = NULL, "
            "last_error_code = :error_code, "
            "available_at = CASE WHEN attempt_count >= max_attempts THEN available_at "
            "ELSE :now + make_interval(secs => LEAST(300, POWER(2, GREATEST(attempt_count, 1))::int)) "
            "END "
            "WHERE id = :id AND locked_by = :worker_id AND status = 'claimed' "
            "RETURNING status"
        ),
        {
            "id": job_id,
            "worker_id": worker_id,
            "now": now,
            "error_code": error_code,
        },
    )
    return str(status) if status is not None else None
