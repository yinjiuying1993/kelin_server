"""Plan remote notifications with DND and daily cap. Spec §§8.11, 17.2.

Does not send APNs. Promise stays iOS-local and is suppressed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.notifications import (
    DAILY_ACTIVE_NOTIFICATION_LIMIT,
    NOTIFICATION_PLAN_EVENT,
    SERVER_LOCAL_ONLY_TYPES,
    care_is_eligible,
    fixed_alert_body,
    in_dnd,
    notification_local_date,
    public_push_payload,
    push_dedupe_key,
    require_remote_type,
    scheduled_for,
)
from app.domain.outbox import DEFAULT_OUTBOX_LEASE_SECONDS
from app.domain.spirit_state import require_aware
from app.repositories import notifications as notice_repo
from app.repositories import outbox as outbox_repo
from app.repositories.outbox import OutboxJob

_SCHEDULER_ROLE = "kelin_scheduler"
_WORKER_ROLE = "kelin_worker"
_LOGGER = get_logger(component="notification_plan")


@dataclass(frozen=True, slots=True)
class NotificationPlanResult:
    job_id: UUID
    owner_id: UUID
    event_type: str
    status: str
    delivery_ids: tuple[UUID, ...]
    fenced: bool = False


async def enqueue_due_notification_plan_jobs(
    session: AsyncSession,
    *,
    now: datetime,
) -> int:
    require_aware(now, field="now")
    await notice_repo.assert_writable_transaction(session)
    return await notice_repo.enqueue_due_notification_plan_jobs(session, now=now)


async def scan_due_notification_plan_jobs(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    async with claimed_transaction(factory, None, db_role=_SCHEDULER_ROLE) as session:
        return await enqueue_due_notification_plan_jobs(session, now=now)


def _payload_type(job: OutboxJob) -> str:
    raw = job.payload.get("type")
    if isinstance(raw, str) and raw:
        return require_remote_type(raw)
    if job.dedupe_key.startswith("push:care:"):
        return "care"
    raise ValueError("notification plan payload type is required")


def _payload_resource(job: OutboxJob) -> UUID | None:
    raw = job.payload.get("resource_id")
    if raw in (None, ""):
        return job.aggregate_id
    return UUID(str(raw))


async def plan_notification_job(
    session: AsyncSession,
    job: OutboxJob,
    *,
    now: datetime,
) -> NotificationPlanResult:
    require_aware(now, field="now")
    await notice_repo.assert_writable_transaction(session)
    if job.owner_id is None:
        fenced = not await outbox_repo.fail_outbox_job(
            session, job_id=job.id, locked_by=job.locked_by, now=now, error="MISSING_OWNER"
        )
        return NotificationPlanResult(
            job_id=job.id,
            owner_id=UUID(int=0),
            event_type="unknown",
            status="dead",
            delivery_ids=(),
            fenced=fenced,
        )
    event_type = _payload_type(job)
    resource_id = _payload_resource(job)
    payload = public_push_payload(event_type, resource_id)
    if set(payload) != {"type", "resource_id"}:
        raise RuntimeError("push payload keys must be type and resource_id")
    _LOGGER.info(
        "notification_plan",
        job_id=str(job.id),
        event_type=event_type,
        alert=fixed_alert_body(event_type),
    )
    prefs = await notice_repo.load_prefs(session, job.owner_id)
    local_day = notification_local_date(now, prefs.timezone if prefs else "Asia/Shanghai")
    dedupe = push_dedupe_key(event_type, resource_id, job.owner_id, local_day)
    status = "pending"
    when = now
    if event_type in SERVER_LOCAL_ONLY_TYPES:
        status = "suppressed"
    elif prefs is None or not prefs.push_on:
        status = "suppressed"
    else:
        quiet = in_dnd(now, prefs.timezone, prefs.dnd_start, prefs.dnd_end)
        used = await notice_repo.count_active_events(
            session,
            owner_id=job.owner_id,
            local_date=local_day,
            timezone_name=prefs.timezone,
        )
        remaining = DAILY_ACTIVE_NOTIFICATION_LIMIT - used
        if event_type == "care":
            eligible = (
                prefs.spirit_status is not None
                and prefs.last_interact_at is not None
                and care_is_eligible(
                    push_on=prefs.push_on,
                    status=prefs.spirit_status,
                    last_interact_at=prefs.last_interact_at,
                    now=now,
                    in_quiet_hours=quiet,
                    remaining_quota=remaining,
                )
            )
            if not eligible:
                status = "suppressed"
        elif remaining <= 0:
            status = "suppressed"
        elif quiet:
            when = scheduled_for(now, prefs.timezone, prefs.dnd_start, prefs.dnd_end)
            later_day = notification_local_date(when, prefs.timezone)
            if later_day != local_day:
                later_used = await notice_repo.count_active_events(
                    session,
                    owner_id=job.owner_id,
                    local_date=later_day,
                    timezone_name=prefs.timezone,
                )
                if later_used >= DAILY_ACTIVE_NOTIFICATION_LIMIT:
                    status = "suppressed"
                else:
                    dedupe = push_dedupe_key(event_type, resource_id, job.owner_id, later_day)
            if status != "suppressed":
                status = "pending"
    devices = ()
    if status != "suppressed" and prefs is not None and prefs.push_on:
        devices = await notice_repo.list_enabled_devices(session, job.owner_id)
        if not devices:
            status = "suppressed"
    delivery_ids: list[UUID] = []
    if status == "suppressed":
        delivery_ids.append(
            await notice_repo.upsert_delivery(
                session,
                owner_id=job.owner_id,
                device_id=None,
                event_type=event_type,
                resource_id=resource_id,
                dedupe_key=dedupe,
                scheduled_for=when,
                status="suppressed",
                now=now,
            )
        )
    else:
        for device in devices:
            delivery_ids.append(
                await notice_repo.upsert_delivery(
                    session,
                    owner_id=job.owner_id,
                    device_id=device.id,
                    event_type=event_type,
                    resource_id=resource_id,
                    dedupe_key=dedupe,
                    scheduled_for=when,
                    status="pending",
                    now=now,
                )
            )
    completed = await outbox_repo.complete_outbox_job(
        session, job_id=job.id, locked_by=job.locked_by, now=now
    )
    return NotificationPlanResult(
        job_id=job.id,
        owner_id=job.owner_id,
        event_type=event_type,
        status=status,
        delivery_ids=tuple(delivery_ids),
        fenced=not completed,
    )


async def process_due_notification_plans(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    limit: int = 20,
    settings: Settings | None = None,
) -> tuple[NotificationPlanResult, ...]:
    require_aware(now, field="now")
    resolved = settings or get_settings()
    worker_id = f"notification-plan:{uuid4()}"
    async with claimed_transaction(factory, None, db_role=_WORKER_ROLE) as session:
        await outbox_repo.reclaim_expired_outbox(session, now=now)
        jobs = await outbox_repo.claim_due_outbox(
            session,
            now=now,
            worker_id=worker_id,
            batch=limit,
            lease_seconds=resolved.outbox_lease_seconds or DEFAULT_OUTBOX_LEASE_SECONDS,
            event_types=(NOTIFICATION_PLAN_EVENT,),
        )
    results: list[NotificationPlanResult] = []
    for job in jobs:
        if job.owner_id is None:
            continue
        user = CurrentUser(id=job.owner_id)
        async with claimed_transaction(factory, user, db_role=_WORKER_ROLE) as session:
            results.append(await plan_notification_job(session, job, now=now))
    return tuple(results)
