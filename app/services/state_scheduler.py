"""Hourly state.settle scan and worker. Spec §§8.6, 17.2.

Scheduler inserts unique `state:{spirit_id}:{due_bucket}` jobs. Worker settles
with an injected clock and records one time_passed fact per job. No device time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.spirit_state import require_aware
from app.repositories import spirit_state as spirit_state_repo
from app.services.growth import record_and_apply
from app.services.spirit_state import settle_spirit_state

_SCHEDULER_ROLE = "kelin_scheduler"
_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class StateScanResult:
    inserted: int


@dataclass(frozen=True, slots=True)
class StateTickResult:
    job_id: UUID
    spirit_id: UUID
    changed: bool
    applied_time_passed: bool


async def enqueue_due_state_jobs(
    session: AsyncSession,
    *,
    now: datetime,
) -> StateScanResult:
    require_aware(now, field="now")
    await spirit_state_repo.assert_writable_transaction(session)
    inserted = await spirit_state_repo.enqueue_due_state_jobs(session, now=now)
    return StateScanResult(inserted=inserted)


async def scan_due_state_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> StateScanResult:
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        return await enqueue_due_state_jobs(session, now=now)


async def process_due_state_ticks(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 50,
) -> tuple[StateTickResult, ...]:
    require_aware(now, field="now")
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        jobs = await spirit_state_repo.claim_due_state_ticks(session, now=now, limit=limit)
    results: list[StateTickResult] = []
    for job in jobs:
        user = CurrentUser(id=job.owner_id)
        async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
            settled = await settle_spirit_state(session, user, now=now)
            applied = False
            if settled.spirit_id is not None:
                recorded = await record_and_apply(
                    session,
                    owner_id=user.id,
                    spirit_id=settled.spirit_id,
                    event_type="time_passed",
                    source_id=job.id,
                    payload={},
                    now=now,
                    touch_interact=False,
                )
                applied = recorded.applied
            await spirit_state_repo.mark_state_tick_done(session, job_id=job.id, now=now)
        results.append(
            StateTickResult(
                job_id=job.id,
                spirit_id=job.spirit_id,
                changed=settled.changed,
                applied_time_passed=applied,
            )
        )
    return tuple(results)
