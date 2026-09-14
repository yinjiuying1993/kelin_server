"""Owner-filtered recall persistence. Spec §§8.7, 11.5."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from app.domain.recall import RECALL_OPERATION
from app.repositories.feed import IdempotencyClaim, MemoryRow

_MEMORY_RETURNING = "id, type, summary, tags, salience, confidence, status, version, created_at"


@dataclass(frozen=True, slots=True)
class LockedRecallSpirit:
    id: uuid.UUID
    version: int
    status: str
    hunger: int
    energy: int
    mood: int
    bond: int
    closeness: int
    timezone: str
    last_interact_at: datetime


@dataclass(frozen=True, slots=True)
class SightMemoryRow:
    id: uuid.UUID
    spirit_id: uuid.UUID
    type: str
    status: str


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("recall cannot write in a read-only transaction")


async def lock_owned_spirit(
    session: AsyncSession, owner_id: uuid.UUID
) -> LockedRecallSpirit | None:
    row = (
        await session.execute(
            text(
                "SELECT s.id, s.version, s.status, s.hunger, s.energy, s.mood, s.bond, "
                "s.closeness, s.last_interact_at, "
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
    return LockedRecallSpirit(
        id=row.id,
        version=int(row.version),
        status=str(row.status),
        hunger=int(row.hunger),
        energy=int(row.energy),
        mood=int(row.mood),
        bond=int(row.bond),
        closeness=int(row.closeness),
        timezone=str(row.timezone),
        last_interact_at=row.last_interact_at,
    )


async def fetch_memory(session: AsyncSession, memory_id: uuid.UUID) -> SightMemoryRow | None:
    row = (
        await session.execute(
            text("SELECT id, spirit_id, type, status FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
    ).first()
    if row is None:
        return None
    return SightMemoryRow(
        id=row.id,
        spirit_id=row.spirit_id,
        type=str(row.type),
        status=str(row.status),
    )


async def insert_relation_memory(
    session: AsyncSession,
    *,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    summary: str,
    salience: int,
    confidence: float,
) -> MemoryRow:
    row = (
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, tags, salience, confidence, status"
                ") VALUES ("
                ":id, :spirit_id, 'relation', :summary, :tags, :salience, :confidence, "
                "'active'"
                ") ON CONFLICT (id) DO NOTHING "
                f"RETURNING {_MEMORY_RETURNING}"
            ).bindparams(bindparam("tags", type_=ARRAY(Text()))),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "summary": summary,
                "tags": ["recall"],
                "salience": salience,
                "confidence": confidence,
            },
        )
    ).first()
    if row is None:
        existing = await fetch_memory_row(session, memory_id)
        if existing is None:
            raise RuntimeError("recall relation memory missing after conflict")
        return existing
    return _memory_row(row)


async def fetch_memory_row(session: AsyncSession, memory_id: uuid.UUID) -> MemoryRow | None:
    row = (
        await session.execute(
            text(f"SELECT {_MEMORY_RETURNING} FROM public.memories WHERE id = :id"),
            {"id": memory_id},
        )
    ).first()
    if row is None:
        return None
    return _memory_row(row)


async def mark_returned_home(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> bool:
    marked = await session.scalar(
        text(
            "UPDATE public.spirits "
            "SET status = 'home', away_until = NULL, study_until = NULL, "
            "has_been_lost = true "
            "WHERE id = :id AND user_id = :user_id AND status = 'lost' "
            "RETURNING id"
        ),
        {"id": spirit_id, "user_id": owner_id},
    )
    return marked is not None


async def claim_recall_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    request_hash: str,
) -> IdempotencyClaim:
    params = {
        "user_id": owner_id,
        "operation": RECALL_OPERATION,
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
    raise RuntimeError("recall idempotency claim failed")


async def fetch_growth_event_id(
    session: AsyncSession, *, source_id: uuid.UUID, event_type: str
) -> uuid.UUID | None:
    existing = await session.scalar(
        text(
            "SELECT id FROM public.growth_events "
            "WHERE source_type = 'recall' AND source_id = :source_id "
            "AND event_type = :event_type"
        ),
        {"source_id": source_id, "event_type": event_type},
    )
    if existing is None:
        return None
    return uuid.UUID(str(existing))


async def complete_recall_idempotency(
    session: AsyncSession,
    owner_id: uuid.UUID,
    client_id: uuid.UUID,
    resource_id: uuid.UUID,
) -> None:
    await session.execute(
        text(
            "UPDATE public.idempotency_records "
            "SET status = 'completed', resource_type = 'recall', "
            "resource_id = :resource_id, completed_at = now() "
            "WHERE user_id = :user_id AND operation = :operation "
            "AND client_id = :client_id"
        ),
        {
            "user_id": owner_id,
            "operation": RECALL_OPERATION,
            "client_id": client_id,
            "resource_id": resource_id,
        },
    )


def _memory_row(row: Any) -> MemoryRow:
    tags = tuple(row.tags) if row.tags is not None else ()
    return MemoryRow(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tags,
        salience=int(row.salience),
        confidence=float(row.confidence),
        status=str(row.status),
        version=int(row.version),
        created_at=row.created_at,
    )
