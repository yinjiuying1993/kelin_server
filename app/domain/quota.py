"""Daily quota windows and limits. Spec §§6.4, 8.10, 15.2.

Limits that are not locked by product/spec stay named defaults so tests can
override them. Timezone changes reuse an unexpired usage_date and cannot mint
a fresh day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from app.domain.bootstrap import utc_z
from app.domain.feed import (
    EMOTION_DAILY_LIMIT,
    FOOD_DAILY_LIMIT,
    KNOWLEDGE_DAILY_LIMIT,
    SIGHT_DAILY_LIMIT,
)
from app.domain.spirit_state import require_aware
from app.schemas.spirit import QuotaUsage

QuotaCapability = Literal[
    "chat",
    "asr",
    "tts",
    "sight",
    "search",
    "food",
    "knowledge",
    "emotion",
    "visit",
    "pact",
]

QUOTA_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "chat",
        "asr",
        "tts",
        "sight",
        "search",
        "food",
        "knowledge",
        "emotion",
        "visit",
        "pact",
    }
)
DEFAULT_TIMEZONE = "Asia/Shanghai"
CHAT_DAILY_LIMIT = 100
ASR_DAILY_LIMIT = 60
TTS_DAILY_LIMIT = 20
SEARCH_DAILY_LIMIT = 20
VISIT_DAILY_LIMIT = 3
PACT_DAILY_LIMIT = 1
RESERVATION_TTL = timedelta(seconds=120)
QUOTA_RESERVE_EVENT = "quota.expire"

DEFAULT_DAILY_LIMITS: Final[dict[str, int]] = {
    "chat": CHAT_DAILY_LIMIT,
    "asr": ASR_DAILY_LIMIT,
    "tts": TTS_DAILY_LIMIT,
    "sight": SIGHT_DAILY_LIMIT,
    "search": SEARCH_DAILY_LIMIT,
    "food": FOOD_DAILY_LIMIT,
    "knowledge": KNOWLEDGE_DAILY_LIMIT,
    "emotion": EMOTION_DAILY_LIMIT,
    "visit": VISIT_DAILY_LIMIT,
    "pact": PACT_DAILY_LIMIT,
}


@dataclass(frozen=True, slots=True)
class QuotaSnapshot:
    capability: str
    used: int
    reserved: int
    limit: int
    usage_date: date
    timezone: str
    version: int


def require_capability(capability: str) -> str:
    if capability not in QUOTA_CAPABILITIES:
        raise ValueError("quota capability is not in the catalog")
    return capability


def daily_limit(capability: str) -> int:
    require_capability(capability)
    return DEFAULT_DAILY_LIMITS[capability]


def coerce_timezone(timezone_name: str | None) -> str:
    name = (timezone_name or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    try:
        ZoneInfo(name)
    except Exception:
        return DEFAULT_TIMEZONE
    return name


def local_usage_date(now: datetime, timezone_name: str) -> date:
    require_aware(now, field="now")
    zone = ZoneInfo(coerce_timezone(timezone_name))
    return now.astimezone(zone).date()


def usage_reset_at(usage_date: date, timezone_name: str) -> datetime:
    zone = ZoneInfo(coerce_timezone(timezone_name))
    local = datetime.combine(usage_date + timedelta(days=1), time.min, tzinfo=zone)
    return local.astimezone(UTC)


def quota_usage(snapshot: QuotaSnapshot) -> QuotaUsage:
    return QuotaUsage(
        capability=snapshot.capability,
        used=snapshot.used,
        limit=snapshot.limit,
        reset_at=utc_z(usage_reset_at(snapshot.usage_date, snapshot.timezone)),
    )


def exceeded_details(snapshot: QuotaSnapshot) -> dict[str, str]:
    return {
        "quota": snapshot.capability,
        "reset_at": quota_usage(snapshot).reset_at,
    }


def reservation_dedupe_key(*, owner_id: UUID, capability: str, source_id: UUID) -> str:
    require_capability(capability)
    return f"quota:{owner_id}:{capability}:{source_id}"
