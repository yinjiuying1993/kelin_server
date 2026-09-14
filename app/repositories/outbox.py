"""Generic outbox claim/lease/fencing. Spec §17.1."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.outbox import DEFAULT_OUTBOX_BATCH, DEFAULT_OUTBOX_LEASE_SECONDS


@dataclass(frozen=True, slots=True)
class OutboxJob:
    id: uuid.UUID
    event_type: str
    aggregate_id: uuid.UUID
    owner_id: uuid.UUID | None
    dedupe_key: str
    payload: dict[str, Any]
    locked_by: str
    attempt_count: int


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("outbox cannot write in a read-only transaction")


async def reclaim_expired_outbox(session: AsyncSession, *, now: datetime) -> int:
    updated = await session.scalar(
        text("SELECT private.reclaim_expired_outbox(:now)"),
        {"now": now},
    )
    return int(updated or 0)


async def claim_due_outbox(
    session: AsyncSession,
    *,
    now: datetime,
    worker_id: str,
    batch: int = DEFAULT_OUTBOX_BATCH,
    lease_seconds: int = DEFAULT_OUTBOX_LEASE_SECONDS,
    event_types: tuple[str, ...] | None = None,
) -> tuple[OutboxJob, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT job_id, event_type, aggregate_id, owner_id, dedupe_key, "
                "payload, locked_by, attempt_count "
                "FROM private.claim_due_outbox("
                ":now, :batch, :worker_id, :lease_seconds, :event_types)"
            ),
            {
                "now": now,
                "batch": batch,
                "worker_id": worker_id,
                "lease_seconds": lease_seconds,
                "event_types": list(event_types) if event_types is not None else None,
            },
        )
    ).mappings()
    jobs: list[OutboxJob] = []
    for row in rows:
        payload = row["payload"]
        jobs.append(
            OutboxJob(
                id=row["job_id"],
                event_type=str(row["event_type"]),
                aggregate_id=row["aggregate_id"],
                owner_id=row["owner_id"],
                dedupe_key=str(row["dedupe_key"]),
                payload=dict(payload) if isinstance(payload, dict) else {},
                locked_by=str(row["locked_by"]),
                attempt_count=int(row["attempt_count"]),
            )
        )
    return tuple(jobs)


async def renew_outbox_lease(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    locked_by: str,
    now: datetime,
    lease_seconds: int = DEFAULT_OUTBOX_LEASE_SECONDS,
) -> bool:
    ok = await session.scalar(
        text(
            "SELECT private.renew_outbox_lease(:job_id, :locked_by, :now, :lease_seconds)"
        ),
        {
            "job_id": job_id,
            "locked_by": locked_by,
            "now": now,
            "lease_seconds": lease_seconds,
        },
    )
    return bool(ok)


async def complete_outbox_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    locked_by: str,
    now: datetime,
) -> bool:
    ok = await session.scalar(
        text("SELECT private.complete_outbox_job(:job_id, :locked_by, :now)"),
        {"job_id": job_id, "locked_by": locked_by, "now": now},
    )
    return bool(ok)


async def fail_outbox_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    locked_by: str,
    now: datetime,
    error: str,
) -> bool:
    ok = await session.scalar(
        text("SELECT private.fail_outbox_job(:job_id, :locked_by, :now, :error)"),
        {"job_id": job_id, "locked_by": locked_by, "now": now, "error": error},
    )
    return bool(ok)


async def release_outbox_until(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    locked_by: str,
    available_at: datetime,
    now: datetime,
) -> bool:
    ok = await session.scalar(
        text(
            "SELECT private.release_outbox_until("
            ":job_id, :locked_by, :available_at, :now)"
        ),
        {
            "job_id": job_id,
            "locked_by": locked_by,
            "available_at": available_at,
            "now": now,
        },
    )
    return bool(ok)
