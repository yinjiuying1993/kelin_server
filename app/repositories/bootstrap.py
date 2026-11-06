"""Owner-filtered bootstrap aggregate queries. Spec §§9.2, 15.2.

Fixed SQL only. Caller must already be in claimed_read_transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.bootstrap import LATEST_MEMORY_LIMIT
from app.domain.room import RoomLetterFact

SightSource = Literal["photo", "location"]
PendingSightStatus = Literal["pending", "processing"]

SPIRIT_SQL = """
SELECT
  s.id, s.name, s.egg, s.invite_code,
  50 AS closeness, 50 AS curiosity, 50 AS sharpness, 50 AS nocturnal, 50 AS stubborn,
  80 AS hunger,
  80 AS energy,
  60 AS mood, 0 AS bond, s.stage,
  COALESCE(a.status, s.status) AS status,
  'formed_image' AS stage_image_key,
  s.snapshot_version, s.version,
  s.onboarding_step, s.onboarding_completed_at, s.hatched_at, s.created_at,
  s.updated_at, s.ordinary_dialogue_rounds,
  COALESCE(a.last_interact_at, s.last_interact_at) AS last_interact_at,
  COALESCE(p.remote_search_on, true) AS remote_search_on,
  COALESCE(p.timezone, 'Asia/Shanghai') AS timezone,
  NULL::text AS primary_emotion_label
FROM public.spirits s
LEFT JOIN public.user_preferences p ON p.user_id = s.user_id
LEFT JOIN public.spirit_awareness a ON a.spirit_id = s.id
WHERE s.user_id = :user_id
"""

PREFS_SQL = """
SELECT remote_search_on
FROM public.user_preferences
WHERE user_id = :user_id
"""

UNREAD_POSTCARD_SQL = """
SELECT p.id, p.created_at
FROM public.postcards p
JOIN public.spirits s ON s.id = p.receiver_spirit_id AND s.user_id = :user_id
WHERE p.read_at IS NULL
ORDER BY p.created_at DESC, p.id DESC
"""

MEMORY_IDS_SQL = """
SELECT m.id
FROM public.memories m
JOIN public.spirits s ON s.id = m.spirit_id AND s.user_id = :user_id
WHERE m.status = 'active'
ORDER BY m.created_at DESC, m.id DESC
LIMIT :limit
"""

ACTIVE_PACT_SQL = """
SELECT pa.id, pa.created_at
FROM public.pacts pa
JOIN public.spirits s ON s.id = pa.spirit_id AND s.user_id = :user_id
WHERE pa.status = 'active'
LIMIT 1
"""

REPORT_SQL = """
SELECT r.id, r.status
FROM public.reports r
JOIN public.spirits s ON s.id = r.spirit_id AND s.user_id = :user_id
WHERE r.report_type = 'seven_day'
LIMIT 1
"""

USAGE_SQL = """
SELECT capability, used, limit_value, usage_date, timezone
FROM public.daily_usage
WHERE user_id = :user_id
  AND ((usage_date + 1)::timestamp AT TIME ZONE timezone) > :now
ORDER BY capability
"""

READONLY_CHECK_SQL = "SHOW transaction_read_only"
ISOLATION_CHECK_SQL = "SHOW transaction_isolation"


@dataclass(frozen=True, slots=True)
class BootstrapSpiritRow:
    id: uuid.UUID
    name: str
    egg: str
    invite_code: str
    closeness: int
    curiosity: int
    sharpness: int
    nocturnal: int
    stubborn: int
    hunger: int
    energy: int
    mood: int
    bond: int
    stage: str
    status: str
    stage_image_key: str | None
    snapshot_version: int
    version: int
    onboarding_step: int
    onboarding_completed_at: datetime | None
    hatched_at: datetime | None
    created_at: datetime
    updated_at: datetime
    ordinary_dialogue_rounds: int
    last_interact_at: datetime
    remote_search_on: bool
    timezone: str
    primary_emotion_label: str | None = None


@dataclass(frozen=True, slots=True)
class PendingSightRow:
    feed_id: uuid.UUID
    source: SightSource
    status: PendingSightStatus
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class UsageRow:
    capability: str
    used: int
    limit: int
    usage_date: date
    timezone: str


@dataclass(frozen=True, slots=True)
class BootstrapAggregateRows:
    spirit: BootstrapSpiritRow | None
    remote_search_on: bool
    pending_sight: PendingSightRow | None
    unread_postcard_ids: tuple[uuid.UUID, ...]
    due_promise: RoomLetterFact | None
    pact_recap: RoomLetterFact | None
    unread_postcard: RoomLetterFact | None
    memory_ids: tuple[uuid.UUID, ...]
    active_pact_id: uuid.UUID | None
    report_id: uuid.UUID | None
    report_status: str | None
    latest_emotion: str | None = None
    quotas: tuple[UsageRow, ...] = field(default_factory=tuple)


async def assert_read_snapshot_transaction(session: AsyncSession) -> None:
    isolation = str(await session.scalar(text(ISOLATION_CHECK_SQL))).lower()
    readonly = str(await session.scalar(text(READONLY_CHECK_SQL))).lower()
    if isolation != "repeatable read":
        raise RuntimeError("bootstrap aggregate requires REPEATABLE READ")
    if readonly != "on":
        raise RuntimeError("bootstrap aggregate requires a read-only transaction")


async def load_bootstrap_aggregate_rows(
    session: AsyncSession,
    owner_id: uuid.UUID,
    *,
    now: datetime,
    require_read_snapshot: bool = True,
) -> BootstrapAggregateRows:
    if require_read_snapshot:
        await assert_read_snapshot_transaction(session)
    params = {"user_id": owner_id}
    spirit_row = (await session.execute(text(SPIRIT_SQL), params)).first()
    spirit: BootstrapSpiritRow | None = None
    remote_search_on = True
    if spirit_row is not None:
        spirit = BootstrapSpiritRow(
            id=spirit_row.id,
            name=str(spirit_row.name),
            egg=str(spirit_row.egg),
            invite_code=str(spirit_row.invite_code),
            closeness=int(spirit_row.closeness),
            curiosity=int(spirit_row.curiosity),
            sharpness=int(spirit_row.sharpness),
            nocturnal=int(spirit_row.nocturnal),
            stubborn=int(spirit_row.stubborn),
            hunger=int(spirit_row.hunger),
            energy=int(spirit_row.energy),
            mood=int(spirit_row.mood),
            bond=int(spirit_row.bond),
            stage=str(spirit_row.stage),
            status=str(spirit_row.status),
            stage_image_key=(
                str(spirit_row.stage_image_key) if spirit_row.stage_image_key else None
            ),
            snapshot_version=int(spirit_row.snapshot_version),
            version=int(spirit_row.version),
            onboarding_step=int(spirit_row.onboarding_step),
            onboarding_completed_at=spirit_row.onboarding_completed_at,
            hatched_at=spirit_row.hatched_at,
            created_at=spirit_row.created_at,
            updated_at=spirit_row.updated_at,
            ordinary_dialogue_rounds=int(spirit_row.ordinary_dialogue_rounds),
            last_interact_at=spirit_row.last_interact_at,
            remote_search_on=bool(spirit_row.remote_search_on),
            timezone=str(spirit_row.timezone),
            primary_emotion_label=(
                str(spirit_row.primary_emotion_label)
                if spirit_row.primary_emotion_label
                else None
            ),
        )
        remote_search_on = spirit.remote_search_on
    else:
        prefs = (await session.execute(text(PREFS_SQL), params)).first()
        if prefs is not None:
            remote_search_on = bool(prefs.remote_search_on)

    pending: PendingSightRow | None = None
    pact_id: uuid.UUID | None = None
    report_id: uuid.UUID | None = None
    report_status: str | None = None
    unread: tuple[uuid.UUID, ...] = ()
    due_promise: RoomLetterFact | None = None
    pact_recap: RoomLetterFact | None = None
    unread_postcard: RoomLetterFact | None = None
    memories: tuple[uuid.UUID, ...] = ()
    quotas: tuple[UsageRow, ...] = ()
    latest_emotion: str | None = None
    if spirit is not None:
        unread_rows = (await session.execute(text(UNREAD_POSTCARD_SQL), params)).all()
        unread = tuple(row.id for row in unread_rows)
        if unread_rows:
            unread_postcard = RoomLetterFact(
                resource_id=unread_rows[0].id,
                occurred_at=unread_rows[0].created_at,
            )
        memories = tuple(
            row.id
            for row in (
                await session.execute(
                    text(MEMORY_IDS_SQL), {"user_id": owner_id, "limit": LATEST_MEMORY_LIMIT}
                )
            ).all()
        )
        usage_rows = (
            await session.execute(text(USAGE_SQL), {"user_id": owner_id, "now": now})
        ).all()
        quotas = tuple(
            UsageRow(
                capability=str(row.capability),
                used=int(row.used),
                limit=int(row.limit_value),
                usage_date=row.usage_date,
                timezone=str(row.timezone),
            )
            for row in usage_rows
        )

    return BootstrapAggregateRows(
        spirit=spirit,
        remote_search_on=remote_search_on,
        pending_sight=pending,
        unread_postcard_ids=unread,
        due_promise=due_promise,
        pact_recap=pact_recap,
        unread_postcard=unread_postcard,
        memory_ids=memories,
        active_pact_id=pact_id,
        report_id=report_id,
        report_status=report_status,
        latest_emotion=latest_emotion,
        quotas=quotas,
    )
