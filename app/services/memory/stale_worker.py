"""Mark low-salience memories stale after 90 days. Rows stay in the table."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import CurrentUser
from app.db.session import claimed_transaction
from app.domain.memory.stale import StaleCandidate, stale_memory_ids
from app.domain.spirit_state import require_aware
from app.repositories import memory as memory_repo

_WORKER_ROLE = "kelin_worker"


async def settle_stale_memories(
    session: AsyncSession,
    *,
    spirit_id: uuid.UUID,
    now: datetime,
) -> tuple[uuid.UUID, ...]:
    require_aware(now, field="now")
    rows = await memory_repo.list_stale_candidates(session, spirit_id=spirit_id)
    chosen = stale_memory_ids(
        tuple(
            StaleCandidate(
                memory_id=memory_id,
                memory_type=memory_type,
                salience=salience,
                last_confirmed_at=confirmed,
                status=status,
            )
            for memory_id, memory_type, salience, confirmed, status in rows
        ),
        now=now,
    )
    await memory_repo.mark_memories_stale(session, spirit_id=spirit_id, memory_ids=chosen)
    return chosen


async def process_stale_memories(
    factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
) -> int:
    require_aware(now, field="now")
    owners = await _spirit_owners(factory)
    marked = 0
    for spirit_id, owner_id in owners:
        async with claimed_transaction(
            factory, CurrentUser(id=owner_id), db_role=_WORKER_ROLE
        ) as session:
            marked += len(await settle_stale_memories(session, spirit_id=spirit_id, now=now))
    return marked


async def _spirit_owners(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[tuple[uuid.UUID, uuid.UUID], ...]:
    async with factory() as session:
        async with session.begin():
            rows = (
                await session.execute(text("SELECT id, user_id FROM public.spirits"))
            ).all()
    return tuple((row.id, row.user_id) for row in rows)
