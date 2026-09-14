"""Owner-filtered feed persistence. Spec §§8.4, 11.1."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.feed import FEED_CREATE_OPERATION
from app.schemas.jsonb import parse_feed_payload, parse_growth_payload

FEEDS_CLIENT_UNIQUE = "uq_feeds_user_id_client_id"
ACTIVE_PROMISE_UNIQUE = "uq_feeds_spirit_one_active_promise"
_FEED_RETURNING = (
    "id, kind, status, version, payload, effect_applied_at, created_at, "
    "promise_status, remind_at, completed_at, rejection_code"
)


@dataclass(frozen=True, slots=True)
class LockedFeedSpirit:
    id: uuid.UUID
    version: int
    hunger: int
    energy: int
    mood: int
    bond: int
    last_interact_at: datetime
    timezone: str


@dataclass(frozen=True, slots=True)
class FeedRow:
    id: uuid.UUID
    kind: str
    status: str
    version: int
    payload: object
    effect_applied_at: datetime | None
    created_at: datetime
    promise_status: str | None
    remind_at: datetime | None
    completed_at: datetime | None
    rejection_code: str | None


@dataclass(frozen=True, slots=True)
class MemoryRow:
    id: uuid.UUID
    type: str
    summary: str
    tags: tuple[str, ...]
    salience: int
    confidence: float
    status: str
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    inserted: bool
    request_hash: str
    status: str
    resource_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AppliedSpirit:
    hunger: int
    energy: int
    mood: int
    bond: int
    version: int
    last_interact_at: datetime


def integrity_constraint_name(exc: BaseException) -> str | None:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = _attr_str(current, "constraint_name")
        diag = getattr(current, "diag", None)
        if diag is not None:
            name = _attr_str(diag, "constraint_name") or name
        if name is not None:
            return name
        nxt = current.__cause__ or current.__context__
        orig = getattr(current, "orig", None)
        if isinstance(orig, BaseException) and orig is not current:
            current = orig
        elif isinstance(nxt, BaseException) and nxt is not current:
            current = nxt
        else:
            current = None
    blob = str(exc).lower()
    if FEEDS_CLIENT_UNIQUE in blob:
        return FEEDS_CLIENT_UNIQUE
    if ACTIVE_PROMISE_UNIQUE in blob:
        return ACTIVE_PROMISE_UNIQUE
    return None


def _attr_str(obj: object, name: str) -> str | None:
    value = getattr(obj, name, None)
    if value is None:
        return None
    text_value = str(value)
    return text_value if text_value else None


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("feed cannot write in a read-only transaction")


async def lock_owned_spirit(session: AsyncSession, owner_id: uuid.UUID) -> LockedFeedSpirit | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.version, s.hunger, s.energy, s.mood, s.bond, s.last_interact_at, "
                "COALESCE(p.timezone, 'Asia/Shanghai') AS timezone "
                "FROM public.spirits s "
                "LEFT JOIN public.user_preferences p ON p.user_id = s.user_id "
                "WHERE s.user_id = :user_id FOR UPDATE OF s"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return LockedFeedSpirit(
        id=row.id,
        version=int(row.version),
        hunger=int(row.hunger),
        energy=int(row.energy),
        mood=int(row.mood),
        bond=int(row.bond),
        last_interact_at=row.last_interact_at,
        timezone=str(row.timezone),
    )


async def insert_accepted_feed(
    session: AsyncSession,
    *,
    feed_id: uuid.UUID,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    client_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    now: datetime,
    effect_applied_at: datetime | None = None,
    promise_status: str | None = None,
    remind_at: datetime | None = None,
    status: str = "accepted",
    rejection_code: str | None = None,
) -> FeedRow:
    parse_feed_payload(kind=kind, payload=payload)
    if effect_applied_at is not None:
        applied_at = effect_applied_at
    elif kind == "promise" or status == "pending":
        applied_at = None
    else:
        applied_at = now
    row = (
        await session.execute(
            text(
                "INSERT INTO public.feeds ("
                "id, user_id, spirit_id, client_id, kind, payload, status, "
                "effect_applied_at, promise_status, remind_at, rejection_code"
                ") VALUES ("
                ":id, :user_id, :spirit_id, :client_id, :kind, CAST(:payload AS jsonb), "
                ":status, :effect_applied_at, :promise_status, :remind_at, :rejection_code"
                f") RETURNING {_FEED_RETURNING}"
            ),
            {
                "id": feed_id,
                "user_id": owner_id,
                "spirit_id": spirit_id,
                "client_id": client_id,
                "kind": kind,
                "payload": json.dumps(payload),
                "status": status,
                "effect_applied_at": applied_at,
                "promise_status": promise_status,
                "remind_at": remind_at,
                "rejection_code": rejection_code,
            },
        )
    ).one()
    return _feed_row(row)


async def fetch_feed_by_client_id(
    session: AsyncSession, *, owner_id: uuid.UUID, client_id: uuid.UUID
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_FEED_RETURNING} FROM public.feeds "
                "WHERE user_id = :user_id AND client_id = :client_id"
            ),
            {"user_id": owner_id, "client_id": client_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def fetch_feed_by_id(
    session: AsyncSession, *, owner_id: uuid.UUID, feed_id: uuid.UUID
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_FEED_RETURNING} FROM public.feeds WHERE user_id = :user_id AND id = :id"
            ),
            {"user_id": owner_id, "id": feed_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def fetch_active_promise(session: AsyncSession, *, spirit_id: uuid.UUID) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_FEED_RETURNING} FROM public.feeds "
                "WHERE spirit_id = :spirit_id AND kind = 'promise' "
                "AND promise_status = 'active' FOR UPDATE"
            ),
            {"spirit_id": spirit_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def lock_owned_feed(
    session: AsyncSession, *, owner_id: uuid.UUID, feed_id: uuid.UUID
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                f"SELECT {_FEED_RETURNING} FROM public.feeds "
                "WHERE user_id = :user_id AND id = :id FOR UPDATE"
            ),
            {"user_id": owner_id, "id": feed_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def update_active_promise(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    feed_id: uuid.UUID,
    payload: dict[str, Any],
    remind_at: datetime,
) -> FeedRow | None:
    parse_feed_payload(kind="promise", payload=payload)
    row = (
        await session.execute(
            text(
                "UPDATE public.feeds SET payload = CAST(:payload AS jsonb), "
                "remind_at = :remind_at, version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND kind = 'promise' "
                "AND promise_status = 'active' "
                f"RETURNING {_FEED_RETURNING}"
            ),
            {
                "payload": json.dumps(payload),
                "remind_at": remind_at,
                "id": feed_id,
                "user_id": owner_id,
            },
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def complete_active_promise(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    feed_id: uuid.UUID,
    now: datetime,
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.feeds SET promise_status = 'completed', "
                "completed_at = :now, effect_applied_at = COALESCE(effect_applied_at, :now), "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND kind = 'promise' "
                "AND promise_status = 'active' "
                f"RETURNING {_FEED_RETURNING}"
            ),
            {"now": now, "id": feed_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def cancel_active_promise(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    feed_id: uuid.UUID,
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.feeds SET promise_status = 'cancelled', status = 'cancelled', "
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND kind = 'promise' "
                "AND promise_status = 'active' "
                f"RETURNING {_FEED_RETURNING}"
            ),
            {"id": feed_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def apply_spirit_deltas(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    spirit_id: uuid.UUID,
    hunger_delta: int,
    energy_delta: int,
    mood_delta: int,
    now: datetime,
    bond_delta: int = 0,
    touch_interact: bool = True,
) -> AppliedSpirit:
    interact_sql = "last_interact_at = GREATEST(last_interact_at, :now), " if touch_interact else ""
    row = (
        await session.execute(
            text(
                "UPDATE public.spirits SET "
                "hunger = GREATEST(0, LEAST(100, hunger + :hunger_delta)), "
                "energy = GREATEST(0, LEAST(100, energy + :energy_delta)), "
                "mood = GREATEST(0, LEAST(100, mood + :mood_delta)), "
                "bond = GREATEST(0, LEAST(100, bond + :bond_delta)), "
                f"{interact_sql}"
                "version = version + 1 "
                "WHERE id = :id AND user_id = :user_id "
                "RETURNING hunger, energy, mood, bond, version, last_interact_at"
            ),
            {
                "hunger_delta": hunger_delta,
                "energy_delta": energy_delta,
                "mood_delta": mood_delta,
                "bond_delta": bond_delta,
                "now": now,
                "id": spirit_id,
                "user_id": owner_id,
            },
        )
    ).first()
    if row is None:
        raise RuntimeError("feed spirit update matched no owner row")
    return AppliedSpirit(
        hunger=int(row.hunger),
        energy=int(row.energy),
        mood=int(row.mood),
        bond=int(row.bond),
        version=int(row.version),
        last_interact_at=row.last_interact_at,
    )


async def insert_knowledge_memory(
    session: AsyncSession,
    *,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    feed_id: uuid.UUID,
    summary: str,
    salience: int,
    confidence: float,
    memory_type: str = "knowledge",
    tags: list[str] | None = None,
) -> MemoryRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, tags, salience, confidence, "
                "status, source_feed_id"
                ") VALUES ("
                ":id, :spirit_id, :memory_type, :summary, :tags, :salience, :confidence, "
                "'active', :source_feed_id"
                ") RETURNING id, type, summary, tags, salience, confidence, status, "
                "version, created_at"
            ).bindparams(bindparam("tags", type_=ARRAY(Text()))),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "memory_type": memory_type,
                "summary": summary,
                "tags": tags or [],
                "salience": salience,
                "confidence": confidence,
                "source_feed_id": feed_id,
            },
        )
    ).one()
    return _memory_row(row)


async def fetch_memory_by_source_feed(
    session: AsyncSession, *, spirit_id: uuid.UUID, feed_id: uuid.UUID
) -> MemoryRow | None:
    row = (
        await session.execute(
            text(
                "SELECT id, type, summary, tags, salience, confidence, status, "
                "version, created_at "
                "FROM public.memories "
                "WHERE spirit_id = :spirit_id AND source_feed_id = :feed_id "
                "AND status <> 'deleted' "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"spirit_id": spirit_id, "feed_id": feed_id},
        )
    ).first()
    if row is None:
        return None
    return _memory_row(row)


async def insert_feed_accepted_event(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    feed_id: uuid.UUID,
    payload: dict[str, int],
    occurred_at: datetime,
) -> uuid.UUID:
    parse_growth_payload(payload)
    inserted = await session.scalar(
        text(
            "INSERT INTO public.growth_events ("
            "spirit_id, source_type, source_id, event_type, payload, occurred_at"
            ") VALUES ("
            ":spirit_id, 'feed', :source_id, 'feed_accepted', CAST(:payload AS jsonb), "
            ":occurred_at"
            ") ON CONFLICT (source_type, source_id, event_type) DO NOTHING "
            "RETURNING id"
        ),
        {
            "spirit_id": spirit_id,
            "source_id": feed_id,
            "payload": json.dumps(payload),
            "occurred_at": occurred_at,
        },
    )
    if inserted is not None:
        return uuid.UUID(str(inserted))
    existing = await session.scalar(
        text(
            "SELECT id FROM public.growth_events "
            "WHERE source_type = 'feed' AND source_id = :source_id "
            "AND event_type = 'feed_accepted'"
        ),
        {"source_id": feed_id},
    )
    if existing is None:
        raise RuntimeError("feed_accepted growth event is missing")
    return uuid.UUID(str(existing))


async def insert_promise_completed_event(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    feed_id: uuid.UUID,
    payload: dict[str, int],
    occurred_at: datetime,
) -> uuid.UUID:
    parse_growth_payload(payload)
    inserted = await session.scalar(
        text(
            "INSERT INTO public.growth_events ("
            "spirit_id, source_type, source_id, event_type, payload, occurred_at"
            ") VALUES ("
            ":spirit_id, 'feed', :source_id, 'promise_completed', CAST(:payload AS jsonb), "
            ":occurred_at"
            ") ON CONFLICT (source_type, source_id, event_type) DO NOTHING "
            "RETURNING id"
        ),
        {
            "spirit_id": spirit_id,
            "source_id": feed_id,
            "payload": json.dumps(payload),
            "occurred_at": occurred_at,
        },
    )
    if inserted is not None:
        return uuid.UUID(str(inserted))
    existing = await session.scalar(
        text(
            "SELECT id FROM public.growth_events "
            "WHERE source_type = 'feed' AND source_id = :source_id "
            "AND event_type = 'promise_completed'"
        ),
        {"source_id": feed_id},
    )
    if existing is None:
        raise RuntimeError("promise_completed growth event is missing")
    return uuid.UUID(str(existing))


async def fetch_growth_event_id(
    session: AsyncSession, *, feed_id: uuid.UUID, event_type: str
) -> uuid.UUID | None:
    existing = await session.scalar(
        text(
            "SELECT id FROM public.growth_events "
            "WHERE source_type = 'feed' AND source_id = :source_id "
            "AND event_type = :event_type"
        ),
        {"source_id": feed_id, "event_type": event_type},
    )
    if existing is None:
        return None
    return uuid.UUID(str(existing))


async def claim_feed_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
    *,
    operation: str = FEED_CREATE_OPERATION,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": operation,
        "client_id": client_id,
        "request_hash": request_hash,
    }
    for _ in range(3):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO public.idempotency_records ("
                    "user_id, operation, client_id, request_hash, status"
                    ") VALUES ("
                    ":user_id, :operation, :client_id, :request_hash, 'in_progress'"
                    ") ON CONFLICT (user_id, operation, client_id) DO NOTHING "
                    "RETURNING request_hash, status, resource_id"
                ),
                params,
            )
        ).first()
        if inserted is not None:
            return IdempotencyClaim(
                inserted=True,
                request_hash=str(inserted.request_hash),
                status=str(inserted.status),
                resource_id=inserted.resource_id,
            )
        existing = (
            await session.execute(
                text(
                    "SELECT request_hash, status, resource_id "
                    "FROM public.idempotency_records "
                    "WHERE user_id = :user_id AND operation = :operation "
                    "AND client_id = :client_id "
                    "FOR UPDATE"
                ),
                params,
            )
        ).first()
        if existing is not None:
            return IdempotencyClaim(
                inserted=False,
                request_hash=str(existing.request_hash),
                status=str(existing.status),
                resource_id=existing.resource_id,
            )
    raise RuntimeError("feed idempotency claim failed")


async def complete_feed_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    feed_id: uuid.UUID,
    *,
    operation: str = FEED_CREATE_OPERATION,
    resource_type: str = "feed",
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = :resource_type, "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": operation,
            "client_id": client_id,
            "resource_id": feed_id,
            "resource_type": resource_type,
        },
    )


async def mark_sight_processing(
    session: AsyncSession, *, owner_id: uuid.UUID, feed_id: uuid.UUID
) -> FeedRow | None:
    row = (
        await session.execute(
            text(
                "UPDATE public.feeds SET status = 'processing', version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND kind = 'sight' "
                "AND status IN ('pending', 'processing') AND effect_applied_at IS NULL "
                f"RETURNING {_FEED_RETURNING}"
            ),
            {"id": feed_id, "user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


async def settle_sight_feed(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    feed_id: uuid.UUID,
    status: str,
    rejection_code: str | None,
    now: datetime,
) -> FeedRow | None:
    if status not in {"accepted", "rejected"}:
        raise ValueError("sight settle status must be accepted or rejected")
    row = (
        await session.execute(
            text(
                "UPDATE public.feeds SET status = :status, rejection_code = :rejection_code, "
                "effect_applied_at = COALESCE(effect_applied_at, :now), version = version + 1 "
                "WHERE id = :id AND user_id = :user_id AND kind = 'sight' "
                "AND effect_applied_at IS NULL "
                f"RETURNING {_FEED_RETURNING}"
            ),
            {
                "id": feed_id,
                "user_id": owner_id,
                "status": status,
                "rejection_code": rejection_code,
                "now": now,
            },
        )
    ).first()
    if row is None:
        return None
    return _feed_row(row)


def is_unique_client_conflict(exc: BaseException) -> bool:
    if not isinstance(exc, DBAPIError):
        name = integrity_constraint_name(exc)
        return name == FEEDS_CLIENT_UNIQUE
    return integrity_constraint_name(exc) == FEEDS_CLIENT_UNIQUE


def is_active_promise_conflict(exc: BaseException) -> bool:
    return integrity_constraint_name(exc) == ACTIVE_PROMISE_UNIQUE


def _feed_row(row: Any) -> FeedRow:
    return FeedRow(
        id=row.id,
        kind=str(row.kind),
        status=str(row.status),
        version=int(row.version),
        payload=row.payload,
        effect_applied_at=row.effect_applied_at,
        created_at=row.created_at,
        promise_status=row.promise_status,
        remind_at=row.remind_at,
        completed_at=row.completed_at,
        rejection_code=row.rejection_code,
    )


def _memory_row(row: Any) -> MemoryRow:
    tags = row.tags
    if tags is None:
        tag_list: tuple[str, ...] = ()
    else:
        tag_list = tuple(str(item) for item in tags)
    confidence = row.confidence
    if isinstance(confidence, Decimal):
        confidence_value = float(confidence)
    else:
        confidence_value = float(confidence)
    return MemoryRow(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tag_list,
        salience=int(row.salience),
        confidence=confidence_value,
        status=str(row.status),
        version=int(row.version),
        created_at=row.created_at,
    )
