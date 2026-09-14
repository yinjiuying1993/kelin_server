"""Independent scheduler and worker ticks. Spec §17.2.

API process must not import this module. T04 APNs send is intentionally absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.session import claimed_transaction
from app.domain.spirit_state import require_aware
from app.repositories import outbox as outbox_repo
from app.services.account_delete import process_due_account_deletes
from app.services.notification_planner import (
    process_due_notification_plans,
    scan_due_notification_plan_jobs,
)
from app.services.report_generate import process_due_report_generates
from app.services.state_scheduler import process_due_state_ticks, scan_due_state_jobs
from app.services.usage_scheduler import process_usage_rollup_jobs, scan_usage_rollup_jobs
from app.services.visit_planner import process_due_visit_plans, scan_due_visit_plan_jobs
from app.services.visit_settle import process_due_visit_settles, scan_due_visit_settle_jobs

_SCHEDULER_ROLE = "kelin_scheduler"


@dataclass(frozen=True, slots=True)
class SchedulerTickResult:
    reclaimed: int
    state: int
    visit_plan: int
    visit_settle: int
    usage_rollup: int
    notification_plan: int


@dataclass(frozen=True, slots=True)
class WorkerTickResult:
    state: int
    visit_plan: int
    visit_settle: int
    usage_rollup: int
    notification_plan: int
    report_generate: int
    account_delete: int


async def run_scheduler_tick(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> SchedulerTickResult:
    require_aware(now, field="now")
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        reclaimed = await outbox_repo.reclaim_expired_outbox(session, now=now)
    state = await scan_due_state_jobs(factory, now=now)
    visit_plan = await scan_due_visit_plan_jobs(factory, now=now)
    visit_settle = await scan_due_visit_settle_jobs(factory, now=now)
    usage = await scan_usage_rollup_jobs(factory, now=now)
    notice = await scan_due_notification_plan_jobs(factory, now=now)
    return SchedulerTickResult(
        reclaimed=reclaimed,
        state=state.inserted,
        visit_plan=visit_plan.inserted,
        visit_settle=visit_settle.inserted,
        usage_rollup=usage,
        notification_plan=notice,
    )


async def run_worker_tick(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    settings: Settings | None = None,
) -> WorkerTickResult:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    state = await process_due_state_ticks(factory, now=now)
    visit_plan = await process_due_visit_plans(factory, now=now)
    visit_settle = await process_due_visit_settles(factory, now=now)
    usage = await process_usage_rollup_jobs(factory, now=now, settings=resolved)
    notice = await process_due_notification_plans(factory, now=now, settings=resolved)
    reports = await process_due_report_generates(factory, now=now, settings=resolved)
    deletions = await process_due_account_deletes(factory, now=now, settings=resolved)
    return WorkerTickResult(
        state=len(state),
        visit_plan=len(visit_plan.job_ids) if visit_plan.job_ids else visit_plan.inserted,
        visit_settle=len(visit_settle),
        usage_rollup=len(usage),
        notification_plan=len(notice),
        report_generate=len(reports),
        account_delete=len(deletions),
    )
