"""Visit plan eligibility and 12h destination keys. Spec §§8.6, 8.9, 17.2."""

from __future__ import annotations

from datetime import UTC, datetime
from re import compile as re_compile
from uuid import UUID

from app.domain.bootstrap import DEFAULT_ROOM_WEATHER
from app.domain.spirit_state import require_aware

MAX_VISIT_DESTINATIONS = 2
VISIT_PLAN_EVENT = "visit.plan"
VISIT_SETTLE_EVENT = "visit.settle"
VISIT_SETTLE_AGGREGATE_TYPE = "visit"
NPC_FALLBACK_STAGE = "formed"
VISIT_PUBLIC_WEATHER = DEFAULT_ROOM_WEATHER
DEFAULT_OUTBOX_LEASE_SECONDS = 120
VISIT_TEMPLATE_VISITOR = "去过{title}，带回一张字条。"
VISIT_TEMPLATE_HOST = "有客人来过。"
VISIT_PLAN_DEDUPE = re_compile(
    r"^visit-plan:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):([0-9]{10})$"
)
VISIT_PLAN_DEDUPE_SQL = (
    r"^visit-plan:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9]{10}$"
)
VISIT_SETTLE_DEDUPE = re_compile(
    r"^visit-settle:([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)
VISIT_SETTLE_DEDUPE_SQL = (
    r"^visit-settle:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def twelve_hour_bucket(now: datetime) -> str:
    """UTC 12h window id YYYYMMDD00 or YYYYMMDD12. Server clock only."""

    moment = require_aware(now, field="now").astimezone(UTC)
    half = 0 if moment.hour < 12 else 12
    return f"{moment.strftime('%Y%m%d')}{half:02d}"


def visit_plan_dedupe_key(spirit_id: UUID, *, now: datetime) -> str:
    return f"visit-plan:{spirit_id}:{twelve_hour_bucket(now)}"


def visit_eligibility_key(
    visitor_spirit_id: UUID, *, now: datetime, destination_index: int
) -> str:
    if destination_index not in (1, 2):
        raise ValueError("destination_index must be 1 or 2")
    return f"visit:{visitor_spirit_id}:{twelve_hour_bucket(now)}:{destination_index}"


def parse_visit_plan_dedupe(dedupe_key: str) -> tuple[UUID, str] | None:
    matched = VISIT_PLAN_DEDUPE.fullmatch(dedupe_key)
    if matched is None:
        return None
    return UUID(matched.group(1)), matched.group(2)


def visit_settle_dedupe_key(visit_id: UUID) -> str:
    return f"visit-settle:{visit_id}"


def parse_visit_settle_dedupe(dedupe_key: str) -> UUID | None:
    matched = VISIT_SETTLE_DEDUPE.fullmatch(dedupe_key)
    if matched is None:
        return None
    return UUID(matched.group(1))


def visit_template_text(*, title: str, for_host: bool) -> str:
    cleaned = title.strip() or "未名"
    if for_host:
        return VISIT_TEMPLATE_HOST
    return VISIT_TEMPLATE_VISITOR.format(title=cleaned)
