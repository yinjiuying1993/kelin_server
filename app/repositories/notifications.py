"""Notification delivery planning persistence. Spec §§8.11, 17.2."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class NotificationPrefs:
    push_on: bool
    timezone: str
    dnd_start: time
    dnd_end: time
    spirit_id: uuid.UUID | None
    spirit_status: str | None
    last_interact_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnabledDevice:
    id: uuid.UUID


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("notification planner cannot write in a read-only transaction")


async def enqueue_due_notification_plan_jobs(session: AsyncSession, *, now: datetime) -> int:
    inserted = await session.scalar(
        text("SELECT private.enqueue_due_notification_plan_jobs(:now)"),
        {"now": now},
    )
    return int(inserted or 0)


async def load_prefs(session: AsyncSession, owner_id: uuid.UUID) -> NotificationPrefs | None:
    row = (
        await session.execute(
            text(
                "SELECT p.push_on, p.timezone, p.dnd_start, p.dnd_end, "
                "s.id AS spirit_id, s.status AS spirit_status, s.last_interact_at "
                "FROM public.user_preferences p "
                "LEFT JOIN public.spirits s ON s.user_id = p.user_id "
                "WHERE p.user_id = :owner_id"
            ),
            {"owner_id": owner_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return NotificationPrefs(
        push_on=bool(row.push_on),
        timezone=str(row.timezone),
        dnd_start=row.dnd_start,
        dnd_end=row.dnd_end,
        spirit_id=row.spirit_id,
        spirit_status=None if row.spirit_status is None else str(row.spirit_status),
        last_interact_at=row.last_interact_at,
    )


async def list_enabled_devices(
    session: AsyncSession, owner_id: uuid.UUID
) -> tuple[EnabledDevice, ...]:
    rows = (
        await session.execute(
            text(
                "SELECT id FROM public.devices "
                "WHERE user_id = :owner_id "
                "AND notifications_enabled "
                "AND invalidated_at IS NULL "
                "ORDER BY last_seen_at DESC, id ASC"
            ),
            {"owner_id": owner_id},
        )
    ).all()
    return tuple(EnabledDevice(id=row.id) for row in rows)


async def count_active_events(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    local_date: date,
    timezone_name: str,
) -> int:
    counted = await session.scalar(
        text(
            "SELECT count(DISTINCT dedupe_key) "
            "FROM public.notification_deliveries "
            "WHERE user_id = :owner_id "
            "AND status IN ('pending', 'claimed', 'sent', 'retry') "
            "AND ((scheduled_for AT TIME ZONE :timezone)::date = :local_date)"
        ),
        {
            "owner_id": owner_id,
            "timezone": timezone_name,
            "local_date": local_date,
        },
    )
    return int(counted or 0)


async def upsert_delivery(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    device_id: uuid.UUID | None,
    event_type: str,
    resource_id: uuid.UUID | None,
    dedupe_key: str,
    scheduled_for: datetime,
    status: str,
    now: datetime,
) -> uuid.UUID:
    delivery_id = await session.scalar(
        text(
            "SELECT private.upsert_notification_delivery("
            ":user_id, :device_id, :event_type, :resource_id, :dedupe_key, "
            ":scheduled_for, :status, :now)"
        ),
        {
            "user_id": owner_id,
            "device_id": device_id,
            "event_type": event_type,
            "resource_id": resource_id,
            "dedupe_key": dedupe_key,
            "scheduled_for": scheduled_for,
            "status": status,
            "now": now,
        },
    )
    if delivery_id is None:
        raise RuntimeError("notification delivery upsert returned no id")
    return uuid.UUID(str(delivery_id))
