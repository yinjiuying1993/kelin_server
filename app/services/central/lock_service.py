"""Lock spirit then module rows in the central order."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.central.snapshot import LockedSpirit, lock_module_rows, lock_spirit


async def lock_aggregate(session: AsyncSession, owner_id: uuid.UUID) -> LockedSpirit | None:
    locked = await lock_spirit(session, owner_id)
    if locked is None:
        return None
    await lock_module_rows(session, locked.id)
    return locked
