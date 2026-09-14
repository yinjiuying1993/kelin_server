"""Notification planning: DND, daily cap, public payload. Spec §§8.11, 17.2."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.quota import DEFAULT_TIMEZONE, coerce_timezone, local_usage_date
from app.domain.spirit_state import require_aware

RemoteNotificationType = Literal["promise", "pact", "postcard", "lost", "care"]
REMOTE_NOTIFICATION_TYPES = frozenset({"promise", "pact", "postcard", "lost", "care"})
SERVER_LOCAL_ONLY_TYPES = frozenset({"promise"})
DAILY_ACTIVE_NOTIFICATION_LIMIT = 2
CARE_IDLE_AFTER = timedelta(hours=18)
DEFAULT_DND_START = time(23, 0)
DEFAULT_DND_END = time(8, 0)
NOTIFICATION_PLAN_EVENT = "notification.plan_user"
PLAN_USER_DEDUPE_PREFIX = "push:"

FIXED_ALERTS: dict[str, str] = {
    "promise": "你有一个约定",
    "pact": "共学有新进展",
    "postcard": "你收到一张明信片",
    "lost": "刻灵走失了",
    "care": "刻灵想你了",
}

ACTIVE_DELIVERY_STATUSES = frozenset({"pending", "claimed", "sent", "retry"})


def require_remote_type(event_type: str) -> str:
    if event_type not in REMOTE_NOTIFICATION_TYPES:
        raise ValueError("notification type is not in the catalog")
    return event_type


def public_push_payload(event_type: str, resource_id: UUID | None) -> dict[str, str | None]:
    require_remote_type(event_type)
    return {
        "type": event_type,
        "resource_id": None if resource_id is None else str(resource_id),
    }


def fixed_alert_body(event_type: str) -> str:
    require_remote_type(event_type)
    body = FIXED_ALERTS[event_type]
    for banned in ("记忆", "对话", "坐标", "Prompt", "user_id"):
        if banned in body:
            raise RuntimeError("fixed alert must stay public")
    return body


def local_clock(now: datetime, timezone_name: str) -> datetime:
    require_aware(now, field="now")
    return now.astimezone(ZoneInfo(coerce_timezone(timezone_name)))


def in_dnd(
    now: datetime,
    timezone_name: str,
    dnd_start: time = DEFAULT_DND_START,
    dnd_end: time = DEFAULT_DND_END,
) -> bool:
    local_t = local_clock(now, timezone_name).time()
    if dnd_start <= dnd_end:
        return dnd_start <= local_t < dnd_end
    return local_t >= dnd_start or local_t < dnd_end


def next_dnd_end(
    now: datetime,
    timezone_name: str,
    dnd_start: time = DEFAULT_DND_START,
    dnd_end: time = DEFAULT_DND_END,
) -> datetime:
    zone = ZoneInfo(coerce_timezone(timezone_name))
    local = local_clock(now, timezone_name)
    end_today = datetime.combine(local.date(), dnd_end, tzinfo=zone)
    if dnd_start > dnd_end:
        if local.time() >= dnd_start:
            return (end_today + timedelta(days=1)).astimezone(UTC)
        return end_today.astimezone(UTC)
    if local.time() < dnd_end:
        return end_today.astimezone(UTC)
    return (end_today + timedelta(days=1)).astimezone(UTC)


def scheduled_for(
    now: datetime,
    timezone_name: str,
    dnd_start: time = DEFAULT_DND_START,
    dnd_end: time = DEFAULT_DND_END,
) -> datetime:
    if in_dnd(now, timezone_name, dnd_start, dnd_end):
        return next_dnd_end(now, timezone_name, dnd_start, dnd_end)
    return require_aware(now, field="now")


def notification_local_date(now: datetime, timezone_name: str):
    return local_usage_date(now, coerce_timezone(timezone_name) or DEFAULT_TIMEZONE)


def push_dedupe_key(
    event_type: str,
    resource_id: UUID | None,
    owner_id: UUID,
    local_date,
) -> str:
    require_remote_type(event_type)
    resource = "none" if resource_id is None else str(resource_id)
    return f"push:{event_type}:{resource}:{owner_id}:{local_date.isoformat()}"


def care_is_eligible(
    *,
    push_on: bool,
    status: str,
    last_interact_at: datetime,
    now: datetime,
    in_quiet_hours: bool,
    remaining_quota: int,
) -> bool:
    if not push_on or in_quiet_hours or remaining_quota <= 0:
        return False
    if status == "lost":
        return False
    idle_from = require_aware(last_interact_at, field="last_interact_at")
    return require_aware(now, field="now") - idle_from >= CARE_IDLE_AFTER
