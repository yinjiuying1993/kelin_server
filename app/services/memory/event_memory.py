"""Read one active memory written for a source event."""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.extract import MemoryRecord


async def load_event_memory(
    session: AsyncSession,
    spirit_id: uuid.UUID,
    event_id: uuid.UUID,
) -> MemoryRecord | None:
    row = (
        await session.execute(
            text(
                "SELECT id, type, summary, tags, salience, confidence, status, version, created_at "
                "FROM public.memories "
                "WHERE spirit_id = :spirit_id AND source_event_id = :event_id "
                "AND status = 'active' LIMIT 1"
            ),
            {"spirit_id": spirit_id, "event_id": event_id},
        )
    ).one_or_none()
    if row is None:
        return None
    tags = row.tags
    return MemoryRecord(
        id=row.id,
        type=str(row.type),
        summary=str(row.summary),
        tags=tuple(str(item) for item in tags) if tags else (),
        salience=int(row.salience),
        confidence=float(row.confidence),
        status=str(row.status),
        version=int(row.version),
        created_at=row.created_at,
    )
