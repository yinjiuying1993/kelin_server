"""Daily usage.rollup scan and worker. Spec §§16.6, 17.2.

Scheduler inserts unique `usage-rollup:{usage_date}` jobs. Worker aggregates
ai_usage metadata and emits missed 70/100 alerts. No Prompt, no hard stop.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import claimed_transaction
from app.domain.ai_usage import UsageRollup, utc_usage_date
from app.domain.spirit_state import require_aware
from app.repositories import ai_usage as usage_repo
from app.services.ai_usage import budget_policy

_SCHEDULER_ROLE = "kelin_scheduler"
_WORKER_ROLE = "kelin_worker"
_LOGGER = get_logger(component="usage_rollup")


async def scan_usage_rollup_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    require_aware(now, field="now")
    usage_date = utc_usage_date(now) - timedelta(days=1)
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        inserted = await usage_repo.enqueue_usage_rollup(session, usage_date=usage_date, now=now)
    return 1 if inserted else 0


async def process_usage_rollup_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    settings: Settings | None = None,
    limit: int = 8,
) -> tuple[UsageRollup, ...]:
    require_aware(now, field="now")
    policy = budget_policy(settings or get_settings())
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        jobs = await usage_repo.claim_usage_rollup(session, now=now, limit=limit)
    results: list[UsageRollup] = []
    for job_id, usage_date in jobs:
        async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
            user_count, request_count, total_micros = await usage_repo.rollup_ai_usage(
                session, usage_date=usage_date
            )
            alerts = await usage_repo.emit_due_budget_alerts(
                session,
                usage_date=usage_date,
                budget_micros=policy.daily_budget_micros,
                warning_percent=policy.warning_percent,
                critical_percent=policy.critical_percent,
            )
            await usage_repo.mark_rollup_done(session, job_id=job_id, now=now)
        rollup = UsageRollup(
            usage_date=usage_date,
            user_count=user_count,
            request_count=request_count,
            total_micros=total_micros,
            alerts_emitted=alerts,
        )
        _LOGGER.info(
            "ai_usage.rollup",
            usage_date=usage_date.isoformat(),
            user_count=user_count,
            request_count=request_count,
            total_micros=total_micros,
        )
        results.append(rollup)
    return tuple(results)
