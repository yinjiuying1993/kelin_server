"""Owner-filtered Debug mutations. Spec §14.10. Never BYPASSRLS."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text

from app.repositories.feed import MemoryRow


async def assert_writable_transaction(session: AsyncSession) -> None:
    flag = await session.scalar(text("SHOW transaction_read_only"))
    if str(flag).lower() == "on":
        raise RuntimeError("debug cannot write in a read-only transaction")


async def lock_owned_spirit_id(session: AsyncSession, owner_id: uuid.UUID) -> uuid.UUID | None:
    spirit_id = await session.scalar(
        text("SELECT id FROM public.spirits WHERE user_id = :user_id FOR UPDATE"),
        {"user_id": owner_id},
    )
    if spirit_id is None:
        return None
    return uuid.UUID(str(spirit_id))


async def update_spirit_fields(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    fields: dict[str, Any],
) -> int | None:
    if not fields:
        raise ValueError("debug spirit update requires fields")
    assignments = [f"{column} = :{column}" for column in fields]
    assignments.append("version = version + 1")
    params = {"user_id": owner_id, **fields}
    version = await session.scalar(
        text(
            "UPDATE public.spirits SET "
            + ", ".join(assignments)
            + " WHERE user_id = :user_id RETURNING version"
        ),
        params,
    )
    if version is None:
        return None
    return int(version)


async def insert_memory(
    session: AsyncSession,
    *,
    memory_id: uuid.UUID,
    spirit_id: uuid.UUID,
    memory_type: str,
    summary: str,
    tags: list[str],
    salience: int,
    confidence: float,
    status: str,
    now: datetime,
) -> MemoryRow:
    sealed_at = now if status == "sealed" else None
    deleted_at = now if status == "deleted" else None
    row = (
        await session.execute(
            text(
                "INSERT INTO public.memories ("
                "id, spirit_id, type, summary, tags, salience, confidence, status, "
                "sealed_at, deleted_at"
                ") VALUES ("
                ":id, :spirit_id, :memory_type, :summary, :tags, :salience, :confidence, "
                ":status, :sealed_at, :deleted_at"
                ") RETURNING id, type, summary, tags, salience, confidence, status, "
                "version, created_at"
            ).bindparams(bindparam("tags", type_=ARRAY(Text()))),
            {
                "id": memory_id,
                "spirit_id": spirit_id,
                "memory_type": memory_type,
                "summary": summary,
                "tags": tags,
                "salience": salience,
                "confidence": confidence,
                "status": status,
                "sealed_at": sealed_at,
                "deleted_at": deleted_at,
            },
        )
    ).one()
    tags_value = tuple(row.tags) if row.tags is not None else ()
    return MemoryRow(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tags_value,
        salience=int(row.salience),
        confidence=float(row.confidence),
        status=str(row.status),
        version=int(row.version),
        created_at=row.created_at,
    )


async def reset_spirit(session: AsyncSession, *, owner_id: uuid.UUID, now: datetime) -> int | None:
    version = await session.scalar(
        text(
            "UPDATE public.spirits SET "
            "hunger = 80, energy = 80, mood = 60, bond = 0, "
            "status = 'home', away_until = NULL, study_until = NULL, "
            "last_interact_at = :now, version = version + 1 "
            "WHERE user_id = :user_id RETURNING version"
        ),
        {"user_id": owner_id, "now": now},
    )
    if version is None:
        return None
    return int(version)
