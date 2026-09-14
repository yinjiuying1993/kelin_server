"""Hourly visit.plan scan and worker. Spec §§8.6, 8.9, 17.2.

Scheduler inserts unique `visit-plan:{spirit_id}:{twelve_hour_bucket}` jobs.
Worker calls `private.plan_visits(now)` with no host/user/NPC arguments.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import claimed_transaction
from app.domain.spirit_state import require_aware
from app.domain.visits import twelve_hour_bucket
from app.repositories import visits as visit_repo

_SCHEDULER_ROLE = "kelin_scheduler"
_WORKER_ROLE = "kelin_worker"


@dataclass(frozen=True, slots=True)
class VisitPlanScanResult:
    inserted: int
    schedule_bucket: str


@dataclass(frozen=True, slots=True)
class VisitPlanTickResult:
    inserted: int
    schedule_bucket: str
    job_ids: tuple[UUID, ...]


async def enqueue_due_visit_plan_jobs(
    session: AsyncSession,
    *,
    now: datetime,
) -> VisitPlanScanResult:
    require_aware(now, field="now")
    await visit_repo.assert_writable_transaction(session)
    inserted = await visit_repo.enqueue_due_visit_plan_jobs(session, now=now)
    return VisitPlanScanResult(inserted=inserted, schedule_bucket=twelve_hour_bucket(now))


async def scan_due_visit_plan_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> VisitPlanScanResult:
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        return await enqueue_due_visit_plan_jobs(session, now=now)


async def plan_due_visits(
    session: AsyncSession,
    *,
    now: datetime,
) -> VisitPlanTickResult:
    require_aware(now, field="now")
    await visit_repo.assert_writable_transaction(session)
    inserted = await visit_repo.plan_visits(session, now=now)
    return VisitPlanTickResult(
        inserted=inserted,
        schedule_bucket=twelve_hour_bucket(now),
        job_ids=(),
    )


async def handle_visit_plan(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> VisitPlanTickResult:
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        return await plan_due_visits(session, now=now)


async def process_due_visit_plans(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 50,
) -> VisitPlanTickResult:
    require_aware(now, field="now")
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        jobs = await visit_repo.claim_due_visit_plan_jobs(session, now=now, limit=limit)
        planned = await plan_due_visits(session, now=now)
        job_ids: list[UUID] = []
        for job in jobs:
            await visit_repo.mark_visit_plan_done(session, job_id=job.id, now=now)
            job_ids.append(job.id)
    return VisitPlanTickResult(
        inserted=planned.inserted,
        schedule_bucket=planned.schedule_bucket,
        job_ids=tuple(job_ids),
    )
