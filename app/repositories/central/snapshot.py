"""Spirit aggregate lock and one snapshot_version bump."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_MODULE_LOCKS = (
    "spirit_awareness",
    "room_projections",
)


@dataclass(frozen=True, slots=True)
class LockedSpirit:
    id: uuid.UUID
    snapshot_version: int
    version: int


async def lock_spirit(session: AsyncSession, owner_id: uuid.UUID) -> LockedSpirit | None:
    row = (
        await session.execute(
            text(
                "SELECT id, snapshot_version, version FROM public.spirits "
                "WHERE user_id = :user_id FOR UPDATE"
            ),
            {"user_id": owner_id},
        )
    ).first()
    if row is None:
        return None
    return LockedSpirit(
        id=row.id,
        snapshot_version=int(row.snapshot_version),
        version=int(row.version),
    )


async def lock_module_rows(session: AsyncSession, spirit_id: uuid.UUID) -> None:
    for table in _MODULE_LOCKS:
        await session.execute(
            text(f"SELECT spirit_id FROM public.{table} WHERE spirit_id = :id FOR UPDATE"),
            {"id": spirit_id},
        )


async def bump_snapshot_once(
    session: AsyncSession, *, owner_id: uuid.UUID, spirit_id: uuid.UUID
) -> int:
    value = await session.scalar(
        text(
            "UPDATE public.spirits "
            "SET snapshot_version = snapshot_version + 1, version = version + 1 "
            "WHERE id = :id AND user_id = :user_id "
            "RETURNING snapshot_version"
        ),
        {"id": spirit_id, "user_id": owner_id},
    )
    if value is None:
        raise RuntimeError("snapshot_version bump did not return a row")
    return int(value)
